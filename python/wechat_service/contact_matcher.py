"""CRM 联系人与微信候选的联系人匹配。

只负责匹配，不负责从微信内部提取数据。手机号先统一格式再比较，微信号
按大小写不敏感比较；命中多个候选人时交人工核验，不自动选第一个。
"""

from __future__ import annotations


def normalize_phone(phone: str | None) -> str:
    """去掉手机号里的空格、横线、括号等非数字字符。"""
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def is_masked_phone(phone: str | None) -> bool:
    """判断是否为掩码手机号，例如 138****1234。"""
    text = (phone or "").strip()
    return any(ch in text for ch in "*＊xX") and sum(ch.isdigit() for ch in text) >= 4


def match_contact(crm_contact: dict, available_contacts: list[dict]) -> dict:
    """把一条 CRM 联系人匹配到微信候选。

    返回：
        {"status": "matched", "contact": {...}, "matched_by": "phone"}
        {"status": "multiple_matches", "candidates": [...], "matched_by": "phone"}
        {"status": "unmatched", "candidates": [], "reason": "..."}
    """
    raw_phone = crm_contact.get("phone") or ""
    wechat_id = (crm_contact.get("wechat_id") or "").strip()
    phone_usable = bool(normalize_phone(raw_phone)) and not is_masked_phone(raw_phone)
    phone = normalize_phone(raw_phone) if phone_usable else ""

    if not phone and not wechat_id:
        return {
            "status": "unmatched",
            "candidates": [],
            "reason": "缺少可用于匹配的手机号或微信号",
        }

    candidates: list[dict] = []
    matched_by: set[str] = set()

    for contact in available_contacts:
        contact_phone = normalize_phone(contact.get("phone"))
        contact_wechat_id = (contact.get("wechat_id") or "").strip()

        phone_match = bool(phone) and contact_phone == phone
        wechat_match = (
            bool(wechat_id)
            and bool(contact_wechat_id)
            and contact_wechat_id.casefold() == wechat_id.casefold()
        )

        if phone_match or wechat_match:
            candidates.append(contact)
            if phone_match:
                matched_by.add("phone")
            if wechat_match:
                matched_by.add("wechat_id")

    matched_by_list = sorted(matched_by)
    if len(candidates) == 1:
        return {
            "status": "matched",
            "contact": candidates[0],
            "matched_by": matched_by_list,
            "candidates": candidates,
        }
    if len(candidates) > 1:
        return {
            "status": "multiple_matches",
            "candidates": candidates,
            "matched_by": matched_by_list,
            "reason": "命中多个候选人，需人工核验",
        }
    return {
        "status": "unmatched",
        "candidates": [],
        "matched_by": [],
        "reason": "未找到匹配的微信联系人",
    }


def find_duplicates(crm_contacts: list[dict], new_contact: dict) -> list[dict]:
    """检查新增联系人是否与已有联系人重复（手机号或微信号相同）。"""
    phone = normalize_phone(new_contact.get("phone"))
    wechat_id = (new_contact.get("wechat_id") or "").strip().casefold()
    duplicates: list[dict] = []
    for contact in crm_contacts:
        if contact.get("crm_contact_id") == new_contact.get("crm_contact_id"):
            continue
        same_phone = bool(phone) and normalize_phone(contact.get("phone")) == phone
        same_wechat = (
            bool(wechat_id)
            and (contact.get("wechat_id") or "").strip().casefold() == wechat_id
        )
        if same_phone or same_wechat:
            duplicates.append(contact)
    return duplicates
