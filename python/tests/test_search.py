"""好友申请搜索核验的解析测试。

OCR 行用固定样本模拟，只验证解析规则本身；真实窗口读取由 test_probe_live
在运行中的微信上验证。
"""

import pytest

from shared import wechat_types as types
from wechat_service import friend_search, wechat_detector


def _lines(*texts):
    return [
        {"text": text, "x": 0, "y": index * 20, "width": 100, "height": 16}
        for index, text in enumerate(texts)
    ]


def test_match_by_wechat_id():
    contact = {"phone": "", "wechat_id": "zhangsan123"}
    result = friend_search.parse_search_result(
        _lines("张先生", "微信号: zhangsan123", "地区: 上海"), contact
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_MATCHED
    assert result["matched_by"] == ["wechat_id"]


def test_match_by_phone():
    contact = {"phone": "13800138000", "wechat_id": ""}
    result = friend_search.parse_search_result(
        _lines("李女士", "138 0013 8000", "添加到通讯录"), contact
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_MATCHED
    assert result["matched_by"] == ["phone"]


def test_not_found_hint_wins_without_identity():
    contact = {"phone": "13800138000", "wechat_id": ""}
    result = friend_search.parse_search_result(
        _lines("搜索: 13800138000", "该用户不存在"), contact
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_NOT_FOUND
    assert result["not_found_hint"] is True


def test_not_found_with_garbled_prefix():
    """真实 OCR 会把「无法找到」识别成「无氵去找到」，仍要判为未找到。"""
    contact = {"phone": "", "wechat_id": "10086"}
    result = friend_search.parse_search_result(
        _lines("0 10086", "无氵去找到该用户 ， 适检查你垣写的账号否正确 。"), contact
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_NOT_FOUND
    assert result["not_found_hint"] is True


def test_exact_wechat_id_with_single_card_is_matched():
    """按微信号精确搜索时，卡片不一定展示微信号，唯一结果即可认定。"""
    contact = {"phone": "", "wechat_id": "Z25713a"}
    result = friend_search.parse_search_result(
        _lines("添加朋友", "Z2S71 3a", "这欣怡犯困 ，", "添加到通讯录"),
        contact,
        search_key="Z25713a",
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_MATCHED
    assert result["matched_by"] == ["search_key"]


def test_multiple_cards_is_ambiguous():
    contact = {"phone": "", "wechat_id": "Z25713a"}
    result = friend_search.parse_search_result(
        _lines("添加朋友", "甲", "添加到通讯录", "乙", "添加到通讯录"),
        contact,
        search_key="Z25713a",
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_AMBIGUOUS


def test_single_card_without_identifier_key_needs_review():
    """10086 既不是手机号也不是合法微信号，不能凭一张卡片认定。"""
    contact = {"phone": "", "wechat_id": "10086"}
    result = friend_search.parse_search_result(
        _lines("欣怡犯困", "添加到通讯录"), contact, search_key="10086"
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_NEEDS_REVIEW


def test_multiple_ids_is_ambiguous():
    contact = {"phone": "", "wechat_id": "zhangsan123"}
    result = friend_search.parse_search_result(
        _lines("微信号: zhangsan123", "微信号: lisi456"), contact
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_AMBIGUOUS


def test_id_mismatch_needs_review():
    contact = {"phone": "", "wechat_id": "zhangsan123"}
    result = friend_search.parse_search_result(
        _lines("王五", "微信号: wangwu789"), contact
    )
    assert result["outcome"] == types.SEARCH_OUTCOME_NEEDS_REVIEW
    assert "不一致" in result["reason"]


def test_empty_result_needs_review():
    contact = {"phone": "13800138000", "wechat_id": ""}
    result = friend_search.parse_search_result(_lines("添加朋友", "搜索"), contact)
    assert result["outcome"] == types.SEARCH_OUTCOME_NEEDS_REVIEW


def test_masked_phone_not_used_for_search():
    contact = {"phone": "138****8000", "wechat_id": "zhangsan123"}
    assert friend_search.build_search_key(contact) == "zhangsan123"


def test_phone_preferred_over_wechat_id():
    contact = {"phone": "138-0013-8000", "wechat_id": "zhangsan123"}
    assert friend_search.build_search_key(contact) == "13800138000"


def test_fallback_used_when_contact_has_no_key():
    assert friend_search.build_search_key({}, fallback="  abc123 ") == "abc123"


def test_suggested_status_mapping():
    assert (
        friend_search.SUGGESTED_STATUS[types.SEARCH_OUTCOME_MATCHED]
        == types.TASK_PENDING_CONFIRMATION
    )
    assert (
        friend_search.SUGGESTED_STATUS[types.SEARCH_OUTCOME_NOT_FOUND]
        == types.TASK_NOT_FOUND
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("微信号：zhangsan123", "zhangsan123"),
        ("微信号: wxid_abcdef123456", "wxid_abcdef123456"),
        ("微信号：中文昵称", None),
        ("通讯录", None),
    ],
)
def test_extract_account(text, expected):
    assert wechat_detector.extract_account(text) == expected
