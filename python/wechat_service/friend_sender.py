"""好友申请发送动作。

用户显式确认后，由 Python 完成单条申请的两个点击：
「添加到通讯录」→「确定」。不做批量、不做重试、不发送第二条。

点击依赖 OCR 给出的文字坐标，因此只适用于当前「添加朋友」窗口；
如果按钮或弹窗文字识别不到，会直接报错，不会盲目点击屏幕。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time
from datetime import datetime
from difflib import SequenceMatcher

from shared import wechat_types as types

from . import (
    contact_reader,
    friend_search,
    screen_reader,
    task_manager,
    wechat_detector,
)
from .database import WeChatDatabase
from .ui_adapter import WeChatUiAdapter

logger = logging.getLogger(__name__)

ADD_BUTTON_TEXT = "添加到通讯录"
DIALOG_HINTS = ("申请添加朋友", "发送添加朋友申请")
CONFIRM_TEXT = "确定"
TEXT_MATCH_THRESHOLD = 0.6
CLICK_INTERVAL_SECONDS = 1.2

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class SendError(RuntimeError):
    """发送好友申请失败，例如按钮未识别、弹窗未出现。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def find_text_line(lines: list[dict], hint: str, threshold: float = TEXT_MATCH_THRESHOLD) -> dict | None:
    """在一组 OCR 行里找最接近目标文字的按钮。"""
    best: dict | None = None
    best_score = 0.0
    for line in lines:
        text = contact_reader.normalize_text(line.get("text", ""))
        if not text:
            continue
        score = 1.0 if hint in text else SequenceMatcher(None, text, hint).ratio()
        if score >= threshold and score > best_score:
            best = line
            best_score = score
    return best


def click_line(window: wechat_detector.WindowInfo, line: dict, adapter: WeChatUiAdapter) -> bool:
    """点击 OCR 文本行的中心点。窗口必须先切到前台。"""
    x = int(line["x"] + line["width"] / 2)
    y = int(line["y"] + line["height"] / 2)
    click_point(window, x, y, adapter)
    return True


def click_point(window: wechat_detector.WindowInfo, x: int, y: int, adapter: WeChatUiAdapter) -> None:
    """点击窗口内的相对坐标。"""
    screen_x = int(window.rect[0] + x)
    screen_y = int(window.rect[1] + y)
    if not friend_search.force_foreground(window.hwnd):
        raise SendError("无法把微信窗口切到前台，已停止点击")
    time.sleep(0.2)
    if not user32.SetCursorPos(screen_x, screen_y):
        raise SendError("移动鼠标失败")
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    logger.info("clicked point (%s, %s) in %s", screen_x, screen_y, window.title)


def find_green_button(window: wechat_detector.WindowInfo) -> tuple[int, int] | None:
    """在窗口底部找微信绿色主按钮。

    系统 OCR 对绿底白字的「确定」识别率很低，但按钮底色稳定，
    因此直接按颜色定位，只取窗口最下方的一小条区域，避免点到权限开关。
    """
    image = screen_reader.capture_window(window.hwnd).convert("RGB")
    width, height = image.size
    top = int(height * 0.85)
    crop = image.crop((0, top, width, height))
    count = 0
    sum_x = 0
    sum_y = 0
    for index, (red, green, blue) in enumerate(crop.getdata()):
        if (
            green >= 140
            and red <= 90
            and blue <= 150
            and green - max(red, blue) >= 50
        ):
            sum_x += index % width
            sum_y += top + index // width
            count += 1
    if count < 100:
        return None
    return sum_x // count, sum_y // count


def _find_send_dialog(adapter: WeChatUiAdapter) -> tuple[wechat_detector.WindowInfo, list[dict]] | None:
    """找到「申请添加朋友」弹窗及其 OCR 行。"""
    for window in adapter.list_windows():
        if not window.visible:
            continue
        try:
            lines = adapter.read_text(window.hwnd)
        except Exception:  # 单个窗口识别失败不影响继续找
            continue
        text = "".join(line.get("text", "") for line in lines)
        has_dialog = any(hint in text for hint in DIALOG_HINTS)
        logger.info(
            "scan window %s %r dialog=%s text=%s",
            window.hwnd,
            window.title,
            has_dialog,
            text[:120],
        )
        if has_dialog:
            return window, lines
    return None


def send_friend_request(
    db: WeChatDatabase,
    task_id: int,
    adapter: WeChatUiAdapter | None = None,
    confirmed: bool = False,
) -> dict:
    """点击发送当前任务的好友申请。

    只允许「待确认」且核验结论为 matched 的任务，并且必须 confirmed=True。
    """
    if not confirmed:
        raise SendError("发送好友申请必须由用户显式确认")

    task = db.get_task(task_id)
    if task is None:
        raise SendError(f"任务不存在: {task_id}")
    if task["status"] != types.TASK_PENDING_CONFIRMATION:
        raise SendError(f"任务当前状态为 {task['status']}，只有待确认状态才能发送")

    verification = (task.get("evidence") or {}).get("verification") or {}
    if verification.get("outcome") != types.SEARCH_OUTCOME_MATCHED:
        raise SendError("核验结论不是 matched，不能自动发送")

    adapter = adapter or WeChatUiAdapter()
    window = friend_search.find_add_friend_window(adapter)
    if window is None:
        raise SendError("未找到「添加朋友」窗口，请先打开它")

    dialog = _find_send_dialog(adapter)
    if dialog is None:
        lines = adapter.read_text(window.hwnd)
        add_button = find_text_line(lines, ADD_BUTTON_TEXT)
        if add_button is None:
            # 对方已是好友时微信显示的是「发消息」，没有添加按钮
            if find_text_line(lines, "发消息") is not None:
                raise SendError("对方已经是好友，不需要再发好友申请")
            raise SendError("未识别到「添加到通讯录」按钮，请确认搜索结果仍显示在窗口中")
        click_line(window, add_button, adapter)
        time.sleep(CLICK_INTERVAL_SECONDS)
        dialog = _find_send_dialog(adapter)
        if dialog is None:
            raise SendError("点击「添加到通讯录」后没有出现申请弹窗")

    dialog_window, dialog_lines = dialog
    confirm_button = find_text_line(dialog_lines, CONFIRM_TEXT)
    if confirm_button is not None:
        click_line(dialog_window, confirm_button, adapter)
        button_text = confirm_button.get("text")
    else:
        # 绿色主按钮上的白字通常识别不到，改按颜色定位
        point = find_green_button(dialog_window)
        if point is None:
            raise SendError("申请弹窗里既没识别到「确定」，也没找到绿色主按钮")
        click_point(dialog_window, point[0], point[1], adapter)
        button_text = "green_button"
    time.sleep(0.5)
    dialog_still_open = True
    for _ in range(10):
        if not user32.IsWindow(dialog_window.hwnd):
            dialog_still_open = False
            break
        time.sleep(0.3)
    if dialog_still_open:
        logger.warning("send dialog still open after confirm click: %s", dialog_window.hwnd)

    send_evidence = {
        "confirmed": True,
        "clicked_at": _now(),
        "dialog_closed": not dialog_still_open,
        "window_title": dialog_window.title,
        "button_text": button_text,
        "note": "Python 点击发送，未做重试",
    }

    evidence = dict(task.get("evidence") or {})
    evidence["send"] = send_evidence
    db.update_task_evidence(task_id, evidence)

    if dialog_still_open:
        raise SendError("点击「确定」后申请弹窗仍未关闭，请人工检查微信是否已发送")

    task = task_manager.transition(
        db,
        task_id=task_id,
        to_status=types.TASK_SUBMITTED,
        actor=types.ACTOR_USER,
        note="Python 自动化点击「添加到通讯录」并「确定」",
        confirmed=True,
    )
    return {"task": task, "send": send_evidence}
