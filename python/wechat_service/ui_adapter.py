"""微信界面适配层。

能力全部来自只读手段：窗口枚举、窗口截图、系统 OCR，以及对 UIA 控件树的
能力探测。这里刻意不实现鼠标键盘注入，也不自动发送好友申请，避免触发
微信风控和平台限制。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from . import screen_reader, wechat_detector
from .wechat_detector import WindowInfo

logger = logging.getLogger(__name__)

UIA_MAX_DEPTH = 4
UIA_MAX_NODES = 300

# 微信的辅助功能接口可能长时间不响应，探测必须带硬超时，不能把接口拖死
UIA_PROBE_TIMEOUT = 4.0


class WeChatUiAdapter:
    """把微信界面读取能力收口到一个类里，方便以后替换适配实现。"""

    def list_windows(self) -> list[WindowInfo]:
        pids = wechat_detector.list_wechat_pids()
        return wechat_detector.ui_windows(wechat_detector.enumerate_windows(pids))

    def find_window(self, hwnd: int | None = None, title: str | None = None) -> WindowInfo | None:
        for window in self.list_windows():
            if hwnd is not None and window.hwnd == hwnd:
                return window
            if title is not None and window.title == title:
                return window
        return None

    def main_window(self) -> WindowInfo | None:
        return wechat_detector.pick_main_window(self.list_windows())

    def read_text(self, hwnd: int) -> list[dict]:
        """读取指定窗口的界面文字。"""
        return screen_reader.ocr_window(hwnd)

    def screenshot(self, hwnd: int) -> bytes:
        return screen_reader.screenshot_bytes(hwnd)

    def probe(self, hwnd: int) -> dict:
        """探测单个窗口能读到什么：UIA 节点数 + OCR 文本行。"""
        report: dict = {
            "hwnd": hwnd,
            "uia": {"available": False, "nodes": 0, "verdict": "unknown", "error": None},
            "ocr": {"available": False, "line_count": 0, "lines": [], "error": None},
        }

        uia_result = probe_uia(hwnd)
        report["uia"] = uia_result

        try:
            lines = self.read_text(hwnd)
            report["ocr"] = {
                "available": True,
                "line_count": len(lines),
                "lines": lines[:50],
                "error": None,
            }
        except Exception as exc:
            report["ocr"]["error"] = str(exc)

        if report["uia"]["nodes"] <= 1 and report["ocr"]["available"]:
            report["verdict"] = "uia_unavailable_use_ocr"
        elif report["uia"]["nodes"] > 1:
            report["verdict"] = "uia_available"
        else:
            report["verdict"] = "unreadable"
        return report


def probe_uia(hwnd: int, timeout: float = UIA_PROBE_TIMEOUT) -> dict:
    """带硬超时的 UIA 探测，超时直接判定不可用，避免阻塞接口。"""
    holder: dict = {}

    def worker() -> None:
        try:
            holder.update(_probe_uia_inner(hwnd))
        except Exception as exc:  # 探测线程里不允许抛到外面
            holder.update(
                {
                    "available": False,
                    "nodes": 0,
                    "verdict": "uia_unavailable",
                    "error": str(exc),
                }
            )

    thread = threading.Thread(target=worker, name="uia-probe", daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        return {
            "available": False,
            "nodes": 0,
            "verdict": "uia_unavailable",
            "error": f"UIA 探测超过 {timeout}s 未返回，微信未响应辅助功能接口",
        }
    return holder or {
        "available": False,
        "nodes": 0,
        "verdict": "uia_unavailable",
        "error": "UIA 探测无结果",
    }


def _probe_uia_inner(hwnd: int) -> dict:
    """统计窗口 UIA 控件树节点数，用于判断该版本是否支持 UIA 读取。

    uiautomation 1.x 在 Python 3.13 下需要先把自带 DLL 目录加入搜索路径。
    """
    result = {"available": False, "nodes": 0, "verdict": "unknown", "error": None}
    try:
        import uiautomation
    except ImportError as exc:
        result["error"] = f"uiautomation 未安装：{exc}"
        result["verdict"] = "uia_unavailable"
        return result

    bin_dir = Path(uiautomation.__file__).resolve().parent / "bin"
    if bin_dir.is_dir() and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(bin_dir))

    try:
        control = uiautomation.ControlFromHandle(hwnd)
        if control is None:
            result["error"] = "无法从窗口句柄创建 UIA 控件"
            result["verdict"] = "uia_unavailable"
            return result

        nodes = 1
        queue = [(control, 0)]
        while queue and nodes < UIA_MAX_NODES:
            current, depth = queue.pop(0)
            if depth >= UIA_MAX_DEPTH:
                continue
            try:
                children = current.GetChildren()
            except Exception:
                continue
            for child in children:
                nodes += 1
                queue.append((child, depth + 1))
                if nodes >= UIA_MAX_NODES:
                    break

        result["available"] = True
        result["nodes"] = nodes
        result["verdict"] = "uia_unavailable" if nodes <= 1 else "uia_available"
    except Exception as exc:
        result["error"] = str(exc)
        result["verdict"] = "uia_unavailable"
    return result
