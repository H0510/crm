"""窗口截图与系统 OCR 读取。

微信 4.x 是 Qt5 自绘界面，UIA 控件树基本为空，界面文字只能通过截图 + OCR 获取。
这里只做读取，不向微信注入任何输入事件。
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import logging
import re
import tempfile
import uuid
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

OCR_LANGUAGE = "zh-Hans-CN"
OCR_SCALE = 2
PW_RENDERFULLCONTENT = 2

_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_MULTI_SPACE = re.compile(r"[ \t\u3000]+")


class WindowCaptureError(RuntimeError):
    """窗口无法截图，通常是句柄失效或窗口不在可见状态。"""


class OcrUnavailable(RuntimeError):
    """系统 OCR 不可用：winsdk 缺失或系统缺少对应语言包。"""


user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.PrintWindow.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.DeleteDC.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [
    wt.HDC,
    wt.HBITMAP,
    wt.UINT,
    wt.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def capture_window(hwnd: int) -> Image.Image:
    """把窗口画面截成 PIL 图像。

    窗口必须处于可见状态；托盘里隐藏的主窗口会得到全黑图像，这里直接报错，
    不返回不可用的空白图。
    """
    if not user32.IsWindow(hwnd):
        raise WindowCaptureError(f"窗口句柄无效: {hwnd}")

    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise WindowCaptureError(f"读取窗口位置失败: {hwnd}")

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise WindowCaptureError("窗口尺寸为 0，可能已最小化")

    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise WindowCaptureError("获取屏幕 DC 失败")

    hdc_mem = None
    hbitmap = None
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        if not hdc_mem or not hbitmap:
            raise WindowCaptureError("创建兼容位图失败")
        gdi32.SelectObject(hdc_mem, hbitmap)

        printed = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        if not printed:
            printed = user32.PrintWindow(hwnd, hdc_mem, 0)
        if not printed:
            raise WindowCaptureError("PrintWindow 失败，窗口可能被遮挡或已最小化")

        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height  # 负数表示自上而下
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0

        buffer = ctypes.create_string_buffer(width * height * 4)
        scanned = gdi32.GetDIBits(
            hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(header), 0
        )
        if scanned != height:
            raise WindowCaptureError("读取位图像素失败")

        image = Image.frombuffer(
            "RGBA", (width, height), buffer, "raw", "BGRA", 0, 1
        ).convert("RGB")
    finally:
        if hbitmap:
            gdi32.DeleteObject(hbitmap)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

    if _is_blank(image):
        raise WindowCaptureError("截图为全黑，窗口可能不可见或已最小化")
    return image


def _is_blank(image: Image.Image) -> bool:
    """判断截图是否全黑，用来识别隐藏窗口截出的空图。"""
    grayscale = image.convert("L")
    histogram = grayscale.histogram()
    bright_pixels = sum(histogram[20:])
    total_pixels = image.width * image.height
    return bright_pixels < total_pixels * 0.005


def _join_text(text: str) -> str:
    """合并汉字之间被 OCR 拆出的空格，其余空白压成一个。"""
    cleaned = _CJK_SPACE.sub("", text)
    return _MULTI_SPACE.sub(" ", cleaned).strip()


async def _recognize_async(image_path: str, language: str) -> list[dict]:
    try:
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确错误
        raise OcrUnavailable("winsdk 未安装，无法调用系统 OCR") from exc

    engine = OcrEngine.try_create_from_language(Language(language))
    if engine is None:
        raise OcrUnavailable(f"系统 OCR 不支持语言 {language}，请安装对应语言包")

    storage_file = await StorageFile.get_file_from_path_async(image_path)
    stream = await storage_file.open_async(FileAccessMode.READ)
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
    finally:
        stream.close()

    lines: list[dict] = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        left = min(word.bounding_rect.x for word in words)
        top = min(word.bounding_rect.y for word in words)
        right = max(word.bounding_rect.x + word.bounding_rect.width for word in words)
        bottom = max(word.bounding_rect.y + word.bounding_rect.height for word in words)
        lines.append(
            {
                "text": _join_text(line.text),
                "x": int(left),
                "y": int(top),
                "width": int(right - left),
                "height": int(bottom - top),
            }
        )
    return lines


def ocr_image(image: Image.Image, language: str = OCR_LANGUAGE) -> list[dict]:
    """对 PIL 图像做 OCR，返回带坐标的文本行。

    小字号直接识别容易漏字，统一放大 OCR_SCALE 倍，坐标再换算回原图。
    """
    scale = OCR_SCALE
    if scale != 1:
        image = image.resize(
            (max(1, image.width * scale), max(1, image.height * scale)),
            Image.LANCZOS,
        )

    temp_dir = Path(tempfile.gettempdir())
    temp_path = temp_dir / f"wechat_ocr_{uuid.uuid4().hex}.png"
    try:
        image.save(temp_path, format="PNG")
        lines = asyncio.run(_recognize_async(str(temp_path), language))
    finally:
        temp_path.unlink(missing_ok=True)

    if scale != 1:
        for line in lines:
            line["x"] = int(line["x"] / scale)
            line["y"] = int(line["y"] / scale)
            line["width"] = int(line["width"] / scale)
            line["height"] = int(line["height"] / scale)
    return lines


def ocr_window(hwnd: int, language: str = OCR_LANGUAGE) -> list[dict]:
    """截图并识别指定窗口，返回文本行。"""
    return ocr_image(capture_window(hwnd), language=language)


def screenshot_bytes(hwnd: int, image_format: str = "PNG") -> bytes:
    """截图并编码为字节流，供接口直接返回给前端预览。"""
    from io import BytesIO

    buffer = BytesIO()
    capture_window(hwnd).save(buffer, format=image_format)
    return buffer.getvalue()
