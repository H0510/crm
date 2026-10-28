"""从微信界面读取联系人候选。

微信 4.x 是自绘界面，联系人字段只能靠 OCR 获取。这里按手机号、微信号、
昵称三类规则抽取候选，并给出置信度；低置信度的候选只作为人工核验的参考，
不会直接写进 CRM 客户资料。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from . import wechat_detector
from .ui_adapter import WeChatUiAdapter

logger = logging.getLogger(__name__)

PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
MASKED_PHONE_PATTERN = re.compile(r"(?<!\d)(1\d{2}[\*＊xX]{3,6}\d{4})(?!\d)")
WECHAT_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])(wxid_[A-Za-z0-9_-]{6,})")

# 微信界面上的固定文案，不作为昵称候选
UI_LABELS = {
    "微信",
    "通讯录",
    "添加朋友",
    "搜索",
    "搜索微信号或者手机号",
    "新的朋友",
    "群聊",
    "标签",
    "公众号",
    "服务号",
    "收藏",
    "朋友圈",
    "聊天",
    "返回",
    "发送",
    "取消",
    "确定",
    "更多",
    "设置",
    "文件传输助手",
    "置顶",
    "消息免打扰",
}

# 允许扫描的窗口标题；为空表示扫描所有 Qt 界面窗口
READABLE_WINDOW_TITLES = ("微信", "通讯录", "添加朋友", "新的朋友", "详细资料")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


UI_LABEL_FUZZY_THRESHOLD = 0.75


def contact_line_is_ui_text(text: str) -> bool:
    """判断一行文字是否只是微信界面固定文案（OCR 近似识别也算）。"""
    normalized = normalize_text(text)
    if not normalized:
        return True
    if normalized in UI_LABELS:
        return True
    for label in UI_LABELS:
        if len(label) >= 3 and wechat_detector.fuzzy_hit(
            normalized, label, UI_LABEL_FUZZY_THRESHOLD
        ):
            return True
    return False


def classify_text(text: str) -> tuple[str, str, str, str]:
    """把一行文本拆成 (昵称, 手机号, 微信号, 置信度)。"""
    normalized = normalize_text(text)
    if not normalized:
        return "", "", "", "low"

    phone_match = PHONE_PATTERN.search(normalized)
    masked_match = MASKED_PHONE_PATTERN.search(normalized)
    wechat_match = WECHAT_ID_PATTERN.search(normalized)

    if phone_match:
        phone = phone_match.group(1)
        nickname = normalize_text(normalized.replace(phone, ""))
        return nickname, phone, (wechat_match.group(1) if wechat_match else ""), "high"

    if wechat_match:
        wechat_id = wechat_match.group(1)
        nickname = normalize_text(normalized.replace(wechat_id, ""))
        return nickname, "", wechat_id, "high"

    if masked_match:
        # 掩码手机号不足以唯一确定客户，只能作为人工核验线索
        phone = masked_match.group(1)
        nickname = normalize_text(normalized.replace(phone, ""))
        return nickname, phone, "", "medium"

    if normalized in UI_LABELS:
        return "", "", "", "skip"

    if 1 <= len(normalized) <= 30 and not any(ch.isdigit() for ch in normalized):
        return normalized, "", "", "low"

    return "", "", "", "skip"


def scan_contacts(
    adapter: WeChatUiAdapter | None = None,
    hwnds: list[int] | None = None,
) -> dict:
    """扫描微信可见窗口，抽取联系人候选。

    返回结构：{"candidates": [...], "windows": [...], "errors": [...]}
    """
    adapter = adapter or WeChatUiAdapter()
    windows = adapter.list_windows()

    if hwnds:
        targets = [window for window in windows if window.hwnd in set(hwnds)]
    else:
        targets = [
            window
            for window in windows
            if window.title in READABLE_WINDOW_TITLES
        ]

    scanned_at = _now()
    candidates: list[dict] = []
    errors: list[dict] = []

    for window in targets:
        try:
            lines = adapter.read_text(window.hwnd)
        except Exception as exc:
            errors.append(
                {
                    "hwnd": window.hwnd,
                    "title": window.title,
                    "error": str(exc),
                }
            )
            continue

        window_text = "".join(line["text"] for line in lines)
        if wechat_detector.is_login_page_text(window_text):
            errors.append(
                {
                    "hwnd": window.hwnd,
                    "title": window.title,
                    "error": "窗口停在登录页，未读取联系人",
                }
            )
            continue

        for line in lines:
            nickname, phone, wechat_id, confidence = classify_text(line["text"])
            if confidence == "skip":
                continue
            if contact_line_is_ui_text(line["text"]):
                continue
            candidates.append(
                {
                    "hwnd": window.hwnd,
                    "window_title": window.title,
                    "raw_text": line["text"],
                    "nickname": nickname,
                    "phone": phone,
                    "wechat_id": wechat_id,
                    "confidence": confidence,
                    "x": line["x"],
                    "y": line["y"],
                    "width": line["width"],
                    "height": line["height"],
                    "scanned_at": scanned_at,
                }
            )

    return {
        "candidates": _deduplicate(candidates),
        "windows": [window.to_dict() for window in targets],
        "errors": errors,
        "scanned_at": scanned_at,
    }


def _deduplicate(candidates: list[dict]) -> list[dict]:
    """按手机号 / 微信号 / 昵称去重，保留置信度更高的一条。"""
    rank = {"high": 3, "medium": 2, "low": 1}
    seen: dict[str, dict] = {}
    for item in candidates:
        key = item["phone"] or item["wechat_id"] or f"nick:{item['nickname']}"
        existing = seen.get(key)
        if existing is None or rank[item["confidence"]] > rank[existing["confidence"]]:
            seen[key] = item
    return list(seen.values())
