"""发送动作的纯逻辑测试；真实点击只在联调时执行。"""

from wechat_service import friend_sender


def _lines(*texts):
    return [
        {"text": text, "x": index * 12, "y": 100, "width": 80, "height": 14}
        for index, text in enumerate(texts)
    ]


def test_find_exact_button():
    line = friend_sender.find_text_line(_lines("取消", "添加到通讯录"), "添加到通讯录")
    assert line is not None
    assert line["text"] == "添加到通讯录"


def test_find_fuzzy_confirm_button():
    line = friend_sender.find_text_line(_lines("确 定"), "确定")
    assert line is not None
    assert line["text"] == "确 定"


def test_missing_button_returns_none():
    assert friend_sender.find_text_line(_lines("取消", "返回"), "确定") is None


def test_send_requires_confirmation(tmp_path):
    from wechat_service.database import WeChatDatabase

    database = WeChatDatabase(tmp_path / "test.db")
    try:
        friend_sender.send_friend_request(database, 1, confirmed=False)
    except friend_sender.SendError as exc:
        assert "显式确认" in str(exc)
    else:
        raise AssertionError("未确认时不应尝试发送")
