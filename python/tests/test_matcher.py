from wechat_service import contact_matcher


CRM_CONTACT = {
    "crm_contact_id": "C10001",
    "name": "张先生",
    "phone": "138-0013-8000",
    "wechat_id": "Zhang_WeChat",
}


def test_normalize_phone_strips_separators():
    assert contact_matcher.normalize_phone("138 0013 8000") == "13800138000"
    assert contact_matcher.normalize_phone("+86 138-0013-8000") == "8613800138000"
    assert contact_matcher.normalize_phone(None) == ""


def test_is_masked_phone():
    assert contact_matcher.is_masked_phone("138****8000") is True
    assert contact_matcher.is_masked_phone("13800138000") is False


def test_match_by_phone():
    candidates = [
        {"nickname": "张先生", "phone": "13800138000", "wechat_id": ""},
        {"nickname": "李女士", "phone": "13900139000", "wechat_id": ""},
    ]
    result = contact_matcher.match_contact(CRM_CONTACT, candidates)
    assert result["status"] == "matched"
    assert result["contact"]["nickname"] == "张先生"
    assert "phone" in result["matched_by"]


def test_match_by_wechat_id_is_case_insensitive():
    candidates = [{"nickname": "张先生", "phone": "", "wechat_id": "zhang_wechat"}]
    result = contact_matcher.match_contact(CRM_CONTACT, candidates)
    assert result["status"] == "matched"
    assert result["matched_by"] == ["wechat_id"]


def test_multiple_matches_requires_manual_review():
    candidates = [
        {"nickname": "张先生", "phone": "13800138000", "wechat_id": ""},
        {"nickname": "张先生（小号）", "phone": "13800138000", "wechat_id": ""},
    ]
    result = contact_matcher.match_contact(CRM_CONTACT, candidates)
    assert result["status"] == "multiple_matches"
    assert len(result["candidates"]) == 2


def test_unmatched():
    candidates = [{"nickname": "李女士", "phone": "13900139000", "wechat_id": ""}]
    result = contact_matcher.match_contact(CRM_CONTACT, candidates)
    assert result["status"] == "unmatched"
    assert result["candidates"] == []


def test_masked_phone_cannot_be_used_for_matching():
    contact = {"crm_contact_id": "C1", "phone": "138****8000", "wechat_id": ""}
    candidates = [{"nickname": "张先生", "phone": "138****8000"}]
    result = contact_matcher.match_contact(contact, candidates)
    assert result["status"] == "unmatched"
    assert "缺少可用于匹配" in result["reason"]


def test_find_duplicates():
    existing = [
        {"crm_contact_id": "C2", "phone": "13800138000", "wechat_id": ""},
        {"crm_contact_id": "C3", "phone": "", "wechat_id": "Zhang_WeChat"},
        {"crm_contact_id": "C4", "phone": "13700137000", "wechat_id": ""},
    ]
    duplicates = contact_matcher.find_duplicates(existing, CRM_CONTACT)
    ids = {item["crm_contact_id"] for item in duplicates}
    assert ids == {"C2", "C3"}
