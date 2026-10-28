import pytest

from wechat_service.profile_collector import (
    normalize_phone,
    parse_profile,
    profile_key,
    recover_wechat_id,
)


def line(text: str, x: int, y: int, width: int = 80, height: int = 12) -> dict:
    return {"text": text, "x": x, "y": y, "width": width, "height": height}


REAL_PAGE = [
    line("朋友资料", 427, 193, 56),
    line("备注", 427, 223, 28),
    line("2，22#宝", 501, 223, 55),
    line("电话", 427, 248, 27),
    line("1 SS76481018", 502, 249, 89),
    line("昵称：见录", 503, 125, 59),
    line("徵信号：Feb03-Ari-z", 503, 143, 115),
    line("通过群聊添加", 501, 427, 83),
]


def test_parse_real_profile_with_ocr_phone_error():
    result = parse_profile(REAL_PAGE, hwnd=1, window_title="微信")
    assert result["wechat_id"] == "Feb03-Ari-z"
    assert result["phone"] == "15576481018"
    assert result["nickname"] == "见录"
    assert result["remark"] == "2，22#宝"

    import json

    fields = json.loads(result["fields_json"])
    assert fields["phone"]["confidence"] == "medium"
    assert fields["phone"]["raw"] == "1 SS76481018"
    assert fields["wechat_id"]["confidence"] == "high"


def test_parse_label_and_value_on_separate_rows():
    lines = [
        line("微信号", 427, 140, 35),
        line("Z25713a", 505, 141, 60),
        line("电话", 427, 170, 25),
        line("15576481018", 505, 171, 90),
    ]
    result = parse_profile(lines)
    assert result["wechat_id"] == "Z25713a"
    assert result["phone"] == "15576481018"


def test_parse_without_phone_leaves_it_empty():
    lines = [
        line("昵称：见录", 503, 125, 59),
        line("徵信号：Feb03-Ari-z", 503, 143, 115),
    ]
    result = parse_profile(lines)
    assert result["phone"] == ""
    assert result["wechat_id"] == "Feb03-Ari-z"


def test_masked_phone_is_not_stored():
    result = normalize_phone("138****1234")
    assert result["phone"] == ""
    assert result["raw"] == "138****1234"


def test_invalid_wechat_id_is_dropped():
    result = parse_profile([line("微信号：中文昵称", 503, 143, 100)])
    assert result["wechat_id"] == ""


def test_profile_key_prefers_wechat_id():
    assert profile_key({"wechat_id": "ABC123", "nickname": "甲"}) == "wechat:abc123"
    assert profile_key({"nickname": "甲", "phone": "13800000000"}) == (
        "name:甲|phone:13800000000"
    )
    assert profile_key({}) == ""


def test_nickname_falls_back_to_top_line_and_placeholder_remark_is_empty():
    lines = [
        line("AIRE 正文", 503, 98, 70),
        line("微信号：hudezhil", 503, 125, 100),
        line("备注", 427, 223, 28),
        line("添加备注名", 501, 223, 80),
        line("地区：湖南长沙", 502, 142, 80),
    ]
    result = parse_profile(lines)
    assert result["nickname"] == "AIRE 正文"
    assert result["remark"] == ""
    assert result["wechat_id"] == "hudezhil"


def test_recover_wechat_id_underscore_from_other_scale():
    lines = [line("彳1号，：miss_0621", 503, 125, 120)]
    assert recover_wechat_id("miss0621", lines) == "miss_0621"
    assert recover_wechat_id("miss_0621", lines) == "miss_0621"


@pytest.mark.parametrize(
    ("raw", "expected", "confidence"),
    [
        ("15576481018", "15576481018", "high"),
        ("1 SS76481018", "15576481018", "medium"),
        ("+86 15576481018", "15576481018", "high"),
        ("155-7648-1018", "15576481018", "high"),
        ("12345", "", "low"),
    ],
)
def test_normalize_phone(raw, expected, confidence):
    result = normalize_phone(raw)
    assert result["phone"] == expected
    assert result["confidence"] == confidence
