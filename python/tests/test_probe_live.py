"""真实微信环境的集成测试：微信未运行时自动跳过，不造假数据。"""

import pytest

from wechat_service import contact_reader, wechat_detector
from wechat_service.ui_adapter import WeChatUiAdapter


def _require_wechat_running():
    if not wechat_detector.list_wechat_pids():
        pytest.skip("本机未运行微信，跳过真实环境集成测试")


def test_detect_reports_process_and_windows():
    _require_wechat_running()
    status = wechat_detector.detect(include_ocr=False)
    assert status.running is True
    assert status.pids
    assert status.windows, "进程在运行却没有任何界面窗口，需要人工检查"


def test_probe_reports_adapter_capability():
    _require_wechat_running()
    adapter = WeChatUiAdapter()
    windows = [window for window in adapter.list_windows() if window.visible]
    if not windows:
        pytest.skip("微信窗口当前不可见，无法截图")

    report = adapter.probe(windows[0].hwnd)
    assert report["verdict"] in {"uia_unavailable_use_ocr", "uia_available", "unreadable"}
    if report["ocr"]["available"]:
        assert report["ocr"]["line_count"] >= 0


def test_scan_contacts_returns_structured_result():
    _require_wechat_running()
    result = contact_reader.scan_contacts()
    assert "candidates" in result
    assert "windows" in result
    assert "errors" in result
