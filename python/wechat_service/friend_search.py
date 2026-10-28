"""好友申请的搜索与结果核验。

工作流第 2 步「搜索与核验」：在微信「添加朋友」界面查询手机号或微信号，
再把界面文字读回来做比对。这里刻意只做单条、用户触发的动作：

- 只把搜索词放进剪贴板并聚焦「添加朋友」窗口，用户自己粘贴或由用户显式
  确认后发送一次 Ctrl+V；微信搜索框是边输边搜，不按回车，避免误触
  「申请添加朋友」弹窗；
- 不点击任何按钮，不发送好友申请，不做批量操作；
- 核验结论只作为人工判断依据，不自动推进任务状态。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import re
import time
from datetime import datetime

from shared import wechat_types as types

from . import contact_matcher, contact_reader, task_manager, wechat_detector
from .database import WeChatDatabase
from .screen_reader import WindowCaptureError
from .ui_adapter import WeChatUiAdapter

logger = logging.getLogger(__name__)

ADD_FRIEND_WINDOW_TITLES = ("添加朋友", "Add Contacts")

# 搜索无结果时界面上的提示文案；OCR 常把「无法」识别成「无氵去」，
# 所以额外匹配「找到该用户」「账号是否正确」这类更稳定的后半句
NOT_FOUND_HINTS = (
    "该用户不存在",
    "用户不存在",
    "无法找到",
    "没有找到",
    "搜索不到",
    "找到该用户",
    "账号是否正确",
)

# 「微信号：xxx」标签后面的自定义微信号
WECHAT_ID_LABEL_PATTERN = re.compile(r"微信号\s*[:：]\s*([A-Za-z][-_A-Za-z0-9]{5,30})")

# OCR 常把手机号拆成 138 0013 8000 或 138-0013-8000，这里允许分组间隔
LOOSE_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9](?:[\s-]?\d){9}(?!\d)")

# 微信搜索结果的每张用户卡片都带一个「添加到通讯录」按钮
ADD_FRIEND_BUTTON_HINT = "添加到通讯录"

# 合法微信号：字母开头，6-20 位，只能是字母、数字、下划线、减号
WECHAT_ID_LIKE_PATTERN = re.compile(r"^[A-Za-z][-_A-Za-z0-9]{4,19}$")

# 允许发起核验的任务状态；已提交、已完成的任务不再重新核验
VERIFIABLE_STATUSES = {
    types.TASK_PENDING,
    types.TASK_REVIEWING,
    types.TASK_NOT_FOUND,
    types.TASK_AMBIGUOUS,
    types.TASK_FAILED,
}

SUGGESTED_STATUS = {
    types.SEARCH_OUTCOME_MATCHED: types.TASK_PENDING_CONFIRMATION,
    types.SEARCH_OUTCOME_NOT_FOUND: types.TASK_NOT_FOUND,
    types.SEARCH_OUTCOME_AMBIGUOUS: types.TASK_AMBIGUOUS,
    types.SEARCH_OUTCOME_NEEDS_REVIEW: types.TASK_REVIEWING,
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
user32.SwitchToThisWindow.restype = None
user32.SetWindowPos.argtypes = [
    wt.HWND,
    wt.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.UINT,
]
user32.SetWindowPos.restype = wt.BOOL
user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.EmptyClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]

kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL

SW_RESTORE = 9
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
VK_MENU = 0x12
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
PASTE_WAIT_SECONDS = 1.5


class SearchError(RuntimeError):
    """搜索与核验无法进行，例如窗口不存在或用户未确认输入。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def find_add_friend_window(adapter: WeChatUiAdapter) -> wechat_detector.WindowInfo | None:
    """找到微信「添加朋友」窗口；窗口必须由用户自己打开。"""
    for window in adapter.list_windows():
        title = (window.title or "").strip()
        if title in ADD_FRIEND_WINDOW_TITLES:
            return window
        if "添加朋友" in title:
            return window
    return None


def build_search_key(contact: dict, fallback: str = "") -> str:
    """按手机号 → 微信号 → 任务里记录的关键词的顺序挑一个搜索依据。"""
    raw_phone = contact.get("phone") or ""
    phone = contact_matcher.normalize_phone(raw_phone)
    if phone and not contact_matcher.is_masked_phone(raw_phone):
        return phone
    wechat_id = (contact.get("wechat_id") or "").strip()
    if wechat_id:
        return wechat_id
    return (fallback or "").strip()


def copy_text(text: str) -> None:
    """把文本放进剪贴板，供用户在微信搜索框里粘贴。"""
    if not user32.OpenClipboard(None):
        raise SearchError("打开剪贴板失败，可能被其他程序占用")
    try:
        if not user32.EmptyClipboard():
            raise SearchError("清空剪贴板失败")
        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            raise SearchError("分配剪贴板内存失败")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            raise SearchError("锁定剪贴板内存失败")
        ctypes.memmove(pointer, buffer, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise SearchError("写入剪贴板失败")
        # SetClipboardData 成功后所有权归系统，不能再 GlobalFree
    finally:
        user32.CloseClipboard()


def force_foreground(hwnd: int) -> bool:
    """把窗口切到前台。

    Windows 默认禁止后台进程抢焦点，所以先轻敲一次 Alt 解除前台锁定，
    再把目标窗口线程挂到当前前台线程上；仍失败时用系统切换接口兜底。
    """
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, 5)

    if user32.GetForegroundWindow() == hwnd:
        return True

    # 轻敲 Alt，绕过前台锁定
    user32.keybd_event(VK_MENU, 0, 0, None)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)

    foreground = user32.GetForegroundWindow()
    foreground_tid = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = False
    if foreground_tid and target_tid and foreground_tid != target_tid:
        attached = bool(user32.AttachThreadInput(target_tid, foreground_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(target_tid, foreground_tid, False)

    time.sleep(0.15)
    if user32.GetForegroundWindow() == hwnd:
        return True

    # SwitchToThisWindow 偶尔会被系统拒绝（前台窗口刚抢回焦点），重试几次再判失败
    for _ in range(3):
        user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.2)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


def paste_search_key() -> None:
    """发送一次 Ctrl+V。只在用户显式确认后调用。

    微信搜索框是边输边搜，不需要回车；按回车会选中结果并打开
    「申请添加朋友」弹窗，属于不该出现的副作用。
    """
    user32.keybd_event(VK_CONTROL, 0, 0, None)
    user32.keybd_event(VK_V, 0, 0, None)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, None)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, None)


def _extract_identifiers(text: str) -> tuple[set[str], set[str]]:
    """从一行 OCR 文字里抽出微信号和手机号。"""
    wechat_ids = set(contact_reader.WECHAT_ID_PATTERN.findall(text))
    wechat_ids.update(WECHAT_ID_LABEL_PATTERN.findall(text))
    phones = set(contact_reader.PHONE_PATTERN.findall(text))
    phones.update(contact_reader.MASKED_PHONE_PATTERN.findall(text))
    phones.update(LOOSE_PHONE_PATTERN.findall(text))
    return wechat_ids, phones


def parse_search_result(lines: list[dict], contact: dict, search_key: str = "") -> dict:
    """把「添加朋友」搜索结果 OCR 行解析成核验结论。

    结论只描述界面证据，不代表微信账号真实身份；无法确认时返回
    needs_review，交给人工核对。
    """
    texts = [
        contact_reader.normalize_text(line.get("text", "")) for line in lines
    ]
    joined = "".join(texts)
    not_found_hint = any(
        wechat_detector.fuzzy_hit(joined, hint) for hint in NOT_FOUND_HINTS
    )
    add_button_count = sum(
        1
        for text in texts
        if text and wechat_detector.fuzzy_hit(text, ADD_FRIEND_BUTTON_HINT)
    )
    key_is_identifier = bool(
        WECHAT_ID_LIKE_PATTERN.fullmatch(search_key or "")
        or LOOSE_PHONE_PATTERN.fullmatch(search_key or "")
    )

    target_phone = contact_matcher.normalize_phone(contact.get("phone"))
    phone_usable = bool(target_phone) and not contact_matcher.is_masked_phone(
        contact.get("phone")
    )
    target_wechat_id = (contact.get("wechat_id") or "").strip()

    entries: list[dict] = []
    all_ids: set[str] = set()
    all_phones: set[str] = set()
    matched_by: list[str] = []

    for text in texts:
        if not text or contact_reader.contact_line_is_ui_text(text):
            continue
        wechat_ids, phones = _extract_identifiers(text)
        all_ids.update(wechat_ids)
        all_phones.update(phones)

        line_matched = []
        if phone_usable and any(
            contact_matcher.normalize_phone(phone) == target_phone for phone in phones
        ):
            line_matched.append("phone")
        if target_wechat_id and any(
            wechat_id.casefold() == target_wechat_id.casefold()
            for wechat_id in wechat_ids
        ):
            line_matched.append("wechat_id")
        for item in line_matched:
            if item not in matched_by:
                matched_by.append(item)

        nickname, _, _, confidence = contact_reader.classify_text(text)
        entries.append(
            {
                "raw_text": text,
                "nickname": nickname,
                "wechat_id": ",".join(sorted(wechat_ids)),
                "phone": ",".join(sorted(phones)),
                "confidence": confidence,
                "matched_by": line_matched,
            }
        )

    wechat_matched = "wechat_id" in matched_by
    if not_found_hint and not wechat_matched:
        # 搜索框本身可能就写着手机号，不能把关键词当成命中结果
        outcome = types.SEARCH_OUTCOME_NOT_FOUND
        reason = "微信界面提示未找到该用户"
    elif len(all_ids) > 1 or add_button_count > 1:
        outcome = types.SEARCH_OUTCOME_AMBIGUOUS
        reason = "界面上出现多个候选结果或微信号，需人工确认"
    elif matched_by:
        outcome = types.SEARCH_OUTCOME_MATCHED
        reason = "搜索结果的微信号/手机号与 CRM 联系人一致"
    elif add_button_count == 1 and key_is_identifier:
        # 按手机号/微信号是精确搜索，界面出现唯一一张用户卡片即可认定
        matched_by = ["search_key"]
        outcome = types.SEARCH_OUTCOME_MATCHED
        reason = "按手机号/微信号精确搜索到唯一结果"
    elif all_ids and target_wechat_id:
        outcome = types.SEARCH_OUTCOME_NEEDS_REVIEW
        reason = "识别到的微信号与 CRM 记录不一致，可能是 OCR 误差或找错人"
    elif entries:
        outcome = types.SEARCH_OUTCOME_NEEDS_REVIEW
        reason = "界面有结果，但缺少可精确比对的手机号或微信号"
    else:
        outcome = types.SEARCH_OUTCOME_NEEDS_REVIEW
        reason = "未从界面读取到有效搜索结果"

    return {
        "outcome": outcome,
        "matched_by": matched_by,
        "reason": reason,
        "search_key": search_key,
        "hwnd": 0,
        "window_title": "",
        "checked_at": _now(),
        "entries": entries,
        "lines": [text for text in texts if text],
        "not_found_hint": not_found_hint,
    }


def verify_task(
    db: WeChatDatabase,
    task_id: int,
    adapter: WeChatUiAdapter | None = None,
    auto_input: bool = False,
    confirmed: bool = False,
    copy_to_clipboard: bool = True,
) -> dict:
    """读取「添加朋友」窗口的搜索结果并核验当前任务。

    默认只复制搜索词并聚焦窗口，由用户自己粘贴；只有 auto_input=True 且
    confirmed=True 时才发送一次 Ctrl+V。
    """
    task = db.get_task(task_id)
    if task is None:
        raise SearchError(f"任务不存在: {task_id}")
    if auto_input and not confirmed:
        raise SearchError("自动输入必须由用户显式确认")
    if task["status"] not in VERIFIABLE_STATUSES:
        raise SearchError(f"任务当前状态为 {task['status']}，不能重新核验")

    contact = db.get_contact(task["crm_contact_id"])
    if contact is None:
        raise SearchError(f"任务对应的 CRM 联系人不存在: {task['crm_contact_id']}")

    search_key = build_search_key(contact, fallback=task.get("search_key") or "")
    if not search_key:
        raise SearchError("该联系人没有手机号或微信号，无法搜索")

    adapter = adapter or WeChatUiAdapter()
    window = find_add_friend_window(adapter)
    if window is None:
        raise SearchError("未找到「添加朋友」窗口，请先在微信中打开它并保留在屏幕上")

    active = force_foreground(window.hwnd)
    if auto_input:
        if not active:
            raise SearchError("无法把「添加朋友」窗口切到前台，请手动点击该窗口后重试")
        copy_text(search_key)
        paste_search_key()
        time.sleep(PASTE_WAIT_SECONDS)
    elif copy_to_clipboard:
        copy_text(search_key)

    # 微信在重新搜索时会重建「添加朋友」窗口，旧句柄会失效，需要按标题重新定位
    lines = None
    for _ in range(3):
        window = find_add_friend_window(adapter) or (
            window if user32.IsWindow(window.hwnd) else None
        )
        if window is None:
            raise SearchError("「添加朋友」窗口已关闭，请重新打开后重试")
        try:
            lines = adapter.read_text(window.hwnd)
            break
        except WindowCaptureError:
            time.sleep(0.3)
    if lines is None:
        raise SearchError("读取「添加朋友」窗口失败，窗口可能已关闭")

    verification = parse_search_result(lines, contact, search_key=search_key)
    verification["hwnd"] = window.hwnd
    verification["window_title"] = window.title
    verification["auto_input"] = bool(auto_input)
    verification["clipboard_text"] = (
        search_key if copy_to_clipboard or auto_input else ""
    )
    verification["suggested_status"] = SUGGESTED_STATUS.get(verification["outcome"])

    if task["status"] == types.TASK_PENDING:
        # 搜索本身就是核验的开始，先把任务推进到 reviewing，建议状态才可落地
        task = task_manager.transition(
            db,
            task_id=task_id,
            to_status=types.TASK_REVIEWING,
            actor=types.ACTOR_SYSTEM,
            note="读取搜索结果，进入核验",
        )

    evidence = dict(task.get("evidence") or {})
    evidence["verification"] = verification
    updated = db.update_task_evidence(task_id, evidence)

    return {
        "task": updated,
        "verification": verification,
        "suggested_status": verification["suggested_status"],
    }
