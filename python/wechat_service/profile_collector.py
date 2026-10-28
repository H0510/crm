"""解析微信联系人资料页的 OCR 结果，只读取界面上真实展示的字段。"""

from __future__ import annotations

import json
import re

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

LABEL_ALIASES = {
    "昵称": "nickname",
    "微信号": "wechat_id",
    "徵信号": "wechat_id",
    "微信号码": "wechat_id",
    "备注": "remark",
    "电话": "phone",
    "手机号": "phone",
    "手机": "phone",
    "地区": "region",
    "邮箱": "email",
    "电子邮箱": "email",
    "来源": "source",
}
LABEL_PATTERN = re.compile(
    "(" + "|".join(sorted(LABEL_ALIASES, key=len, reverse=True)) + r")\s*[:：]?\s*(.*)$"
)

WECHAT_ID_WXID = re.compile(r"^wxid_[A-Za-z0-9_-]{4,}$")
WECHAT_ID_CUSTOM = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{5,19}$")
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
MASKED_PHONE = re.compile(r"\d{2,4}\*{2,}\d{2,4}")
EMAIL_PATTERN = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
PROFILE_UI_TEXTS = {
    "朋友资料",
    "朋友圈",
    "更多信息",
    "个性签名",
    "添加时间",
    "发消息",
    "语音聊天",
    "视频聊天",
    "添加备注名",
    "添加备注",
    "备注名",
    "共同群聊",
    "标签",
    "视频号",
    "企业信息",
    "来自",
    "实名",
    # OCR 对分组标题的常见误读
    "朋友料",
    "通过扫一扫添加",
    "通过群聊添加",
    "通过微信号搜索添加",
    "通过手机号添加",
}
REMARK_PLACEHOLDERS = {"添加备注名", "添加备注", "备注名", "名"}
WECHAT_ID_INLINE = re.compile(r"[:：]\s*([A-Za-z][-_A-Za-z0-9]{5,19})")

# 电话 OCR 常见字母误识，仅在手机号校验失败时启用
OCR_DIGITS = {
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "i": "1", "l": "1", "|": "1", "!": "1", "]": "1",
    "Z": "2", "z": "2",
    "E": "3",
    "A": "4",
    "S": "5", "s": "5",
    "G": "6", "b": "6",
    "T": "7",
    "B": "8",
    "g": "9", "q": "9",
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _center_y(line: dict) -> float:
    return float(line.get("y") or 0) + float(line.get("height") or 0) / 2


def _is_label_only(text: str) -> bool:
    compact = _compact(text).rstrip(":：")
    return compact in LABEL_ALIASES


def _clean_value(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"^[:：]\s*", "", value)
    return value.strip()


def _find_right_value(label: dict, lines: list[dict]) -> dict | None:
    """标签同行没有值，取同一水平线右侧最近的一行。"""
    label_y = _center_y(label)
    label_right = float(label.get("x") or 0) + float(label.get("width") or 0)
    candidates = []
    for line in lines:
        if line is label:
            continue
        if _is_label_only(line.get("text", "")):
            continue
        line_x = float(line.get("x") or 0)
        if line_x < label_right + 2:
            continue
        if abs(_center_y(line) - label_y) > 10:
            continue
        candidates.append((line_x, line))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _find_below_value(label: dict, lines: list[dict]) -> dict | None:
    """值换行显示时，取标签正下方最近的一行。"""
    label_x = float(label.get("x") or 0)
    label_bottom = float(label.get("y") or 0) + float(label.get("height") or 0)
    candidates = []
    for line in lines:
        if line is label or _is_label_only(line.get("text", "")):
            continue
        line_x = float(line.get("x") or 0)
        line_y = float(line.get("y") or 0)
        if line_x < label_x or line_x > label_x + 160:
            continue
        gap = line_y - label_bottom
        if gap < -2 or gap > 24:
            continue
        candidates.append((gap, line_x, line))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def extract_profile_fields(lines: list[dict]) -> dict:
    """按标签抽取资料页字段，返回字段值、置信度和 OCR 原文。"""
    fields: dict[str, dict] = {}
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        compact = _compact(text)
        # 只认行首的标签：OCR 会把来源文案识别成「…微信号添加」，
        # 用 search 会把「添加」当成微信号覆盖掉真正读到的值
        match = LABEL_PATTERN.match(compact)
        if not match:
            continue
        key = LABEL_ALIASES[match.group(1)]
        value = _clean_value(match.group(2))
        source_line = line
        if not value:
            value_line = _find_right_value(line, lines)
            if value_line is None:
                value_line = _find_below_value(line, lines)
            if value_line is not None:
                source_line = value_line
                value = _clean_value(value_line.get("text", ""))
        if not value:
            continue
        fields[key] = {
            "value": value,
            "raw": text if source_line is line else f"{text} | {source_line.get('text', '')}",
            "x": int(source_line.get("x") or 0),
            "y": int(source_line.get("y") or 0),
        }
    return fields


def normalize_phone(raw: str) -> dict:
    """清洗电话并把 OCR 字母误识纠正为数字；无法通过校验时返回空号码。"""
    original = (raw or "").strip()
    compact = re.sub(r"[\s\-—–()（）.。·]", "", original)
    if not compact:
        return {"phone": "", "confidence": "low", "raw": original, "corrected": False}
    if MASKED_PHONE.search(compact):
        return {"phone": "", "confidence": "low", "raw": original, "corrected": False}
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("86") and len(compact) == 13:
        compact = compact[2:]

    digits = re.sub(r"\D", "", compact)
    if PHONE_PATTERN.match(digits) and digits == compact:
        return {"phone": digits, "confidence": "high", "raw": original, "corrected": False}

    if any(ch in OCR_DIGITS for ch in compact):
        corrected = "".join(OCR_DIGITS.get(ch, ch) for ch in compact)
        corrected_digits = re.sub(r"\D", "", corrected)
        if PHONE_PATTERN.match(corrected_digits):
            return {
                "phone": corrected_digits,
                "confidence": "medium",
                "raw": original,
                "corrected": True,
            }
    return {"phone": "", "confidence": "low", "raw": original, "corrected": False}


def _repair_id_prefix(value: str) -> str:
    """微信号首字母 O 常被 OCR 读成数字 0，微信号本身不会以数字开头。"""
    if len(value) >= 6 and value[0] == "0":
        return "O" + value[1:]
    return ""


ID_LEADING_JUNK = re.compile(r"^[^A-Za-z0-9]+")


def _wechat_id_result(raw: str) -> dict:
    value = _compact(raw)
    # OCR 常在微信号前面多带一个括号、竖线之类的符号
    value = ID_LEADING_JUNK.sub("", value)
    if WECHAT_ID_WXID.match(value) or WECHAT_ID_CUSTOM.match(value):
        return {"value": value, "confidence": "high", "raw": raw}
    repaired = _repair_id_prefix(value)
    if repaired and (
        WECHAT_ID_WXID.match(repaired) or WECHAT_ID_CUSTOM.match(repaired)
    ):
        return {"value": repaired, "confidence": "medium", "raw": raw}
    return {"value": "", "confidence": "low", "raw": raw}


def _usable_nickname(value: str) -> bool:
    """昵称可能是纯表情，OCR 会读成「0」之类的单字符，这种要退回显示名。"""
    compact = _compact(value)
    return len(compact) >= 2 and any(ch.isalnum() for ch in compact)


_UI_PREFIXES = tuple(
    sorted(set(PROFILE_UI_TEXTS) | set(LABEL_ALIASES), key=len, reverse=True)
)
_LEADING_SYMBOLS = re.compile(r"^[^A-Za-z0-9\u4e00-\u9fff]+")
# 「2024 · 03 · 13」「2025-06-13」「1 个」这类卡片附属信息
_DATE_LIKE = re.compile(r"^\d{4}\s*[·.\-/年]\s*\d{1,2}(\s*[·.\-/月]\s*\d{1,2})?\s*日?$")
_COUNT_LIKE = re.compile(r"^\d+\s*个$")


def _ui_like(text: str) -> bool:
    """把界面文案（朋友圈、添加备注名、微信号：…）排除掉，避免当成昵称。"""
    compact = _LEADING_SYMBOLS.sub("", _compact(text))
    if not compact:
        return True
    if compact in PROFILE_UI_TEXTS or compact in LABEL_ALIASES:
        return True
    if _DATE_LIKE.match(compact) or _COUNT_LIKE.match(compact):
        return True
    # 「通过扫一扫添加」这类来源文案 OCR 经常缺字，按结尾兜底
    if compact.endswith("添加") and ("通" in compact or "涌" in compact):
        return True
    return any(compact.startswith(prefix) for prefix in _UI_PREFIXES)


def recover_wechat_id(value: str, lines: list[dict]) -> str:
    """放大 OCR 常把下划线读成空格；从其他倍率的原文里找回带下划线的写法。"""
    if not value or "_" in value:
        return value
    target = value.replace("_", "").casefold()
    for line in lines:
        match = WECHAT_ID_INLINE.search(line.get("text") or "")
        if not match:
            continue
        candidate = match.group(1)
        if "_" in candidate and candidate.replace("_", "").casefold() == target:
            return candidate
    return value


def _nickname_line(lines: list[dict]) -> dict | None:
    """资料卡顶部只显示昵称，没有「昵称：」标签。

    昵称用卡片里最大的字号渲染（OCR 行高明显高于标签行），先按行高取；
    行高读不出来时再退回按位置 + 界面文案过滤。
    """
    ordered = sorted(lines, key=lambda item: float(item.get("y") or 0))
    for line in ordered:
        text = (line.get("text") or "").strip()
        if float(line.get("height") or 0) < 15:
            continue
        if _ui_like(text) or "：" in text or ":" in text:
            continue
        if _usable_nickname(text):
            return line
    for line in ordered:
        text = (line.get("text") or "").strip()
        if len(_compact(text)) < 2:
            continue
        if _ui_like(text) or "：" in text or ":" in text:
            continue
        return line
    return None


def _fallback_nickname(lines: list[dict]) -> str:
    line = _nickname_line(lines)
    return (line.get("text") or "").strip() if line else ""


ID_ICON_PREFIX = re.compile(r"^[^A-Za-z0-9\u4e00-\u9fff]+")


def _unlabeled_wechat_id(lines: list[dict], nickname: dict | None) -> dict | None:
    """个人资料卡里微信号紧跟昵称、没有「微信号：」标签时按位置补读。

    企业微信联系人的卡片同一位置显示的是企业名（中文），必然过不了微信号格式校验，
    因此这里只接受合法的微信号形态，避免把企业名当成微信号写进库。
    """
    if nickname is None:
        return None
    bottom = float(nickname.get("y") or 0) + float(nickname.get("height") or 0)
    base_x = float(nickname.get("x") or 0)
    for line in sorted(lines, key=lambda item: float(item.get("y") or 0)):
        if line is nickname:
            continue
        top = float(line.get("y") or 0)
        if top < bottom + 3 or top > bottom + 46:
            continue
        if abs(float(line.get("x") or 0) - base_x) > 26:
            continue
        text = (line.get("text") or "").strip()
        compact = _compact(text)
        if not compact or compact in PROFILE_UI_TEXTS or compact in LABEL_ALIASES:
            continue
        if "：" in text or ":" in text:
            continue
        value = ID_ICON_PREFIX.sub("", compact)
        if WECHAT_ID_WXID.match(value) or WECHAT_ID_CUSTOM.match(value):
            return {"value": value, "confidence": "medium", "raw": text}
    return None


def parse_profile(lines: list[dict], hwnd: int = 0, window_title: str = "") -> dict:
    """把资料页 OCR 行转换成采集记录。"""
    extracted = extract_profile_fields(lines)
    nickname_anchor = _nickname_line(lines)
    wechat = _wechat_id_result(extracted.get("wechat_id", {}).get("value", ""))
    if not wechat["value"]:
        guess = _unlabeled_wechat_id(lines, nickname_anchor)
        if guess is not None:
            wechat = guess
    phone = normalize_phone(extracted.get("phone", {}).get("value", ""))
    nickname_field = extracted.get("nickname", {})
    label_nickname = (nickname_field.get("value") or "").strip()
    top_nickname = (
        (nickname_anchor.get("text") or "").strip() if nickname_anchor else ""
    )
    # 顶部显示名（备注/昵称）和通讯录里看到的一致，优先用；
    # 但它必须真的排在「昵称：」标签上方，否则说明取到的是标签区的文字
    label_y = float(nickname_field.get("y") or 0)
    top_y = float(nickname_anchor.get("y") or 0) if nickname_anchor else None
    top_above_label = top_y is not None and (
        not label_nickname or top_y < label_y
    )
    label_ok = _usable_nickname(label_nickname) and not _ui_like(label_nickname)
    top_ok = (
        _usable_nickname(top_nickname)
        and not _ui_like(top_nickname)
        and top_above_label
    )
    if top_ok:
        nickname = top_nickname
    elif label_ok:
        nickname = label_nickname
    else:
        nickname = label_nickname or (
            top_nickname if not _ui_like(top_nickname) else ""
        )
    remark = (extracted.get("remark", {}).get("value") or "").strip()
    if _compact(remark) in REMARK_PLACEHOLDERS:
        remark = ""
    region = (extracted.get("region", {}).get("value") or "").strip()
    email_raw = (extracted.get("email", {}).get("value") or "").strip()
    email = email_raw if EMAIL_PATTERN.match(email_raw) else ""
    source = (extracted.get("source", {}).get("value") or "").strip()
    if not nickname:
        nickname = _fallback_nickname(lines).strip()

    field_results = {
        "nickname": {
            "value": nickname,
            "confidence": "medium" if nickname else "low",
            "raw": extracted.get("nickname", {}).get("raw", ""),
        },
        "wechat_id": wechat,
        "phone": phone,
        "remark": {
            "value": remark,
            "confidence": "medium" if remark else "low",
            "raw": extracted.get("remark", {}).get("raw", ""),
        },
        "region": {
            "value": region,
            "confidence": "medium" if region else "low",
            "raw": extracted.get("region", {}).get("raw", ""),
        },
        "email": {
            "value": email,
            "confidence": "medium" if email else "low",
            "raw": email_raw,
        },
        "source": {
            "value": source,
            "confidence": "medium" if source else "low",
            "raw": extracted.get("source", {}).get("raw", ""),
        },
    }
    confidence = "low"
    for field in field_results.values():
        if CONFIDENCE_RANK.get(field["confidence"], 0) > CONFIDENCE_RANK[confidence]:
            confidence = field["confidence"]

    raw_text = "\n".join((line.get("text") or "").strip() for line in lines if line.get("text"))
    anchor = next((line for line in lines if (line.get("text") or "").strip()), {})
    return {
        "hwnd": int(hwnd),
        "window_title": window_title or "微信",
        "raw_text": raw_text,
        "nickname": nickname,
        "phone": phone["phone"],
        "wechat_id": wechat["value"],
        "remark": remark,
        "region": region,
        "email": email,
        "source": source or "address_book",
        "confidence": confidence,
        "fields_json": json.dumps(field_results, ensure_ascii=False),
        "x": int(anchor.get("x") or 0),
        "y": int(anchor.get("y") or 0),
        "width": int(anchor.get("width") or 0),
        "height": int(anchor.get("height") or 0),
    }


def profile_key(record: dict) -> str:
    """用于判断资料页是否切换；没有有效字段时返回空串。"""
    wechat_id = (record.get("wechat_id") or "").strip().casefold()
    if wechat_id:
        return f"wechat:{wechat_id}"
    nickname = (record.get("nickname") or "").strip().casefold()
    phone = (record.get("phone") or "").strip()
    if nickname or phone:
        return f"name:{nickname}|phone:{phone}"
    return ""
