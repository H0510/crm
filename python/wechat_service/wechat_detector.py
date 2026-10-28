"""微信进程、窗口与登录状态检测。

只做只读检测：进程列表、顶层窗口枚举、登录状态推断。
不注入微信进程，不读取微信本地数据库。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable

import psutil

logger = logging.getLogger(__name__)

PROCESS_NAMES = {"weixin.exe", "wechat.exe"}

# 微信 4.x 采用 Qt5 自绘界面，主窗口与子窗口类名都是 Qt5 前缀
QT_WINDOW_CLASS_PREFIX = "Qt5"

# 内部消息窗口，不属于用户可见界面
INTERNAL_WINDOW_CLASSES = {
    "IME",
    "MSCTFIME UI",
    "Base_PowerMessageWindow",
    "WxTrayIconMessageWindow",
    "Chrome_SystemMessageWindow",
    "DisplayICC_SystemMessageWindow",
}
INTERNAL_WINDOW_CLASS_PREFIXES = ("SoPY_",)

MAIN_WINDOW_TITLES = {"微信", "WeChat"}

NOT_LOGGED_IN_HINTS = ("进入微信", "仅传输文件", "切换账号", "扫码登录", "使用手机扫码登录")
LOGGED_IN_HINTS = (
    "通讯录",
    "朋友圈",
    "收藏",
    "文件传输助手",
    "聊天",
    "公众号",
    "服务号",
    "群聊",
    "新的朋友",
    "标签",
)

# OCR 对界面小字的识别并不稳定（例如「仅传输文件」识别成「仅传蝓文件」），
# 因此登录页特征词采用相似度匹配，而不是严格包含判断。
FUZZY_HINT_THRESHOLD = 0.6

# 微信号在个人信息页/资料卡上以「微信号：xxx」的形式出现，主界面通常不显示
ACCOUNT_PATTERN = re.compile(r"微信号\s*[:：]\s*([A-Za-z][-_A-Za-z0-9]{5,30})")

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool
    minimized: bool
    rect: tuple[int, int, int, int]

    def to_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "title": self.title,
            "class_name": self.class_name,
            "visible": self.visible,
            "minimized": self.minimized,
            "rect": list(self.rect),
        }


@dataclass
class WeChatStatus:
    running: bool
    pids: list[int] = field(default_factory=list)
    windows: list[WindowInfo] = field(default_factory=list)
    main_window: WindowInfo | None = None
    login_state: str = "unknown"
    account: str | None = None
    evidence: list[str] = field(default_factory=list)
    ocr_error: str | None = None
    checked_at: str = ""

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "pids": self.pids,
            "windows": [window.to_dict() for window in self.windows],
            "main_window": self.main_window.to_dict() if self.main_window else None,
            "login_state": self.login_state,
            "account": self.account,
            "evidence": self.evidence,
            "ocr_error": self.ocr_error,
            "checked_at": self.checked_at,
        }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def list_wechat_pids() -> list[int]:
    """返回正在运行的微信进程 pid 列表。"""
    pids: list[int] = []
    for process in psutil.process_iter(["name", "pid"]):
        try:
            name = (process.info.get("name") or "").lower()
            if name in PROCESS_NAMES:
                pids.append(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return sorted(pids)


def enumerate_windows(pids: Iterable[int] | None = None) -> list[WindowInfo]:
    """枚举微信进程的全部顶层窗口，包含隐藏和最小化的窗口。"""
    target = set(pids) if pids is not None else set(list_wechat_pids())
    if not target:
        return []

    windows: list[WindowInfo] = []

    def callback(hwnd, _lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in target:
            return True

        title_length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)

        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, 256)

        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        windows.append(
            WindowInfo(
                hwnd=int(hwnd),
                pid=int(pid.value),
                title=title_buffer.value,
                class_name=class_buffer.value,
                visible=bool(user32.IsWindowVisible(hwnd)),
                minimized=bool(user32.IsIconic(hwnd)),
                rect=(rect.left, rect.top, rect.right, rect.bottom),
            )
        )
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(callback), 0)
    return windows


def is_ui_window(window: WindowInfo) -> bool:
    """过滤掉 IME、托盘、输入法等内部消息窗口，只保留真正的界面窗口。"""
    if window.class_name in INTERNAL_WINDOW_CLASSES:
        return False
    if any(window.class_name.startswith(prefix) for prefix in INTERNAL_WINDOW_CLASS_PREFIXES):
        return False
    left, top, right, bottom = window.rect
    if right - left <= 0 or bottom - top <= 0:
        return False
    return True


def ui_windows(windows: Iterable[WindowInfo]) -> list[WindowInfo]:
    return [window for window in windows if is_ui_window(window)]


def pick_main_window(windows: Iterable[WindowInfo]) -> WindowInfo | None:
    """挑选主窗口：优先标题为“微信”、类名为 Qt5 的窗口，其次取面积最大的 Qt5 窗口。"""
    candidates = [
        window
        for window in windows
        if window.class_name.startswith(QT_WINDOW_CLASS_PREFIX)
    ]
    for candidate in candidates:
        if candidate.title in MAIN_WINDOW_TITLES:
            return candidate
    if candidates:
        return max(
            candidates,
            key=lambda item: (item.rect[2] - item.rect[0])
            * (item.rect[3] - item.rect[1]),
        )
    return None


def _pick_ocr_target(main_window: WindowInfo | None, windows: list[WindowInfo]) -> WindowInfo | None:
    """选择用于登录状态判断的截图窗口：主窗口可见就用主窗口，否则用任意可见界面窗口。"""
    if main_window and main_window.visible and not main_window.minimized:
        return main_window
    for window in windows:
        if window.visible and not window.minimized:
            return window
    return None


def fuzzy_hit(text: str, hint: str, threshold: float = FUZZY_HINT_THRESHOLD) -> bool:
    """在整段 OCR 文字里滑动比对一个特征词，容忍个别字的识别偏差。"""
    if not text or not hint:
        return False
    if hint in text:
        return True
    if len(text) < len(hint):
        return SequenceMatcher(None, text, hint).ratio() >= threshold
    best = 0.0
    for start in range(len(text) - len(hint) + 1):
        window = text[start : start + len(hint)]
        best = max(best, SequenceMatcher(None, window, hint).ratio())
        if best >= threshold:
            return True
    return False


def is_login_page_text(text: str) -> bool:
    """判断一段界面文字是否来自微信登录页。"""
    return any(fuzzy_hit(text, hint) for hint in NOT_LOGGED_IN_HINTS)


def extract_account(ocr_text: str) -> str | None:
    """从界面文字里取当前登录账号（微信号）；界面没展示时返回 None。"""
    match = ACCOUNT_PATTERN.search(ocr_text or "")
    return match.group(1) if match else None


def classify_login_state(
    windows: list[WindowInfo], ocr_text: str
) -> tuple[str, list[str]]:
    """根据窗口与 OCR 文本推断登录状态，判不准返回 unknown。"""
    evidence: list[str] = []

    if not windows:
        return "unknown", ["未发现微信界面窗口"]

    visible = [window for window in windows if window.visible and not window.minimized]
    if not visible:
        evidence.append("微信窗口均不可见（可能已最小化到托盘），无法确认登录状态")

    for hint in NOT_LOGGED_IN_HINTS:
        if fuzzy_hit(ocr_text, hint):
            evidence.append(f"界面出现登录页特征文字（近似匹配：{hint}）")
            return "not_logged_in", evidence

    # 能打开「添加朋友」窗口，本身就说明微信已经登录
    if any((window.title or "").strip() == "添加朋友" for window in windows):
        evidence.append("存在「添加朋友」窗口，说明微信已登录")
        return "logged_in", evidence

    matched_hints = [hint for hint in LOGGED_IN_HINTS if hint in ocr_text]
    if len(matched_hints) >= 2:
        evidence.append("界面出现会话列表特征文字：" + "、".join(matched_hints))
        return "logged_in", evidence

    if not ocr_text:
        evidence.append("截图未识别到文字，无法判断登录状态")
    elif matched_hints:
        evidence.append("仅识别到少量会话特征文字：" + "、".join(matched_hints))
    else:
        evidence.append("界面文字未命中登录/未登录特征词")
    return "unknown", evidence


def detect(include_ocr: bool = True) -> WeChatStatus:
    """完整检测：进程 → 窗口 → 登录状态。"""
    status = WeChatStatus(running=False, checked_at=_now())
    pids = list_wechat_pids()
    if not pids:
        status.evidence.append("未发现 Weixin.exe / WeChat.exe 进程")
        return status

    status.running = True
    status.pids = pids

    all_windows = enumerate_windows(pids)
    status.windows = ui_windows(all_windows)
    status.main_window = pick_main_window(status.windows)
    if status.main_window is None:
        status.evidence.append("进程在运行，但未找到界面窗口（可能停在托盘或正在启动）")
        status.login_state = "unknown"
        return status

    ocr_text = ""
    if include_ocr:
        target = _pick_ocr_target(status.main_window, status.windows)
        if target is None:
            status.evidence.append("没有可截图的可见窗口，跳过 OCR")
        else:
            try:
                from . import screen_reader

                lines = screen_reader.ocr_window(target.hwnd)
                ocr_text = "".join(line["text"] for line in lines)
                status.evidence.append(
                    f"OCR 读取窗口「{target.title or target.class_name}」，识别 {len(lines)} 行"
                )
            except Exception as exc:  # 截图/OCR 失败不影响进程与窗口结论
                status.ocr_error = str(exc)
                status.evidence.append(f"OCR 读取失败：{exc}")

    status.login_state, extra_evidence = classify_login_state(
        status.windows, ocr_text
    )
    status.evidence.extend(extra_evidence)

    account = extract_account(ocr_text)
    if account:
        status.account = account
        status.evidence.append(f"界面识别到当前登录微信号：{account}")
    elif status.login_state == "logged_in":
        status.evidence.append("主界面未展示微信号，当前账号需进入个人信息页确认")
    return status
