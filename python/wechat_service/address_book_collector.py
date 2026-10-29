"""通讯录遍历采集：逐个切换联系人资料页，读取微信号和电话。"""

from __future__ import annotations

import ctypes
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from ctypes import wintypes as wt
from datetime import datetime

from PIL import ImageChops, ImageStat

from . import friend_search, friend_sender, screen_reader, wechat_detector
from .database import WeChatDatabase
from .profile_collector import parse_profile, profile_key, recover_wechat_id
from .ui_adapter import WeChatUiAdapter

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
user32.keybd_event.restype = None

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_WHEEL = 0x0800

UI_LABELS = {
    "通讯录管理",
    "新的朋友",
    "公众号",
    "企业微信联系人",
    "联系人",
    "星标朋友",
    "通讯录",
}

# 通讯录列表里只有联系人行带头像，组织名等分组标题是纯文字行。
# OCR 对「名字 + @微信号」这种彩色排版识别很差，因此改用头像像素定位可点击行。
# 头像在列表里的横向位置是固定像素（列表左边缘再往里约 40px），不随窗口宽度缩放；
# 用宽度的百分比算在窄窗口上会整个偏掉，这里按像素给区间。
AVATAR_BAND_LEFT = 92
AVATAR_BAND_RIGHT = 142
AVATAR_MIN_HEIGHT = 20
AVATAR_MAX_HEIGHT = 58
AVATAR_MIN_HITS = 12
AVATAR_GAP_TOLERANCE = 6
# 白底、浅色头像和列表底色的差异很小，阈值压低到 30 才认得出；
# 纯文字的分组标题行高不到 AVATAR_MIN_HEIGHT，不会因为阈值变低被误判成联系人行
AVATAR_COLOR_DELTA = 30

# 窗口太窄时微信会把右侧资料栏收起来，点联系人拿不到资料卡（表现为扫 0 人）。
# 采集前先把窗口撑到三栏布局的最小尺寸。
LAYOUT_MIN_WIDTH = 860
LAYOUT_MIN_HEIGHT = 600
LAYOUT_TARGET = (900, 640)
SWP_NOZORDER_NOACTIVATE = 0x0004 | 0x0010


class CollectError(Exception):
    pass


class AutomationBusyError(CollectError):
    pass


# 批量添加后续也复用这把锁，保证同时只有一个任务占用微信窗口
AUTOMATION_LOCK = threading.Lock()


def _press_key(vk: int, times: int = 1, delay: float = 0.12) -> None:
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, None)
        time.sleep(0.03)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)
        time.sleep(delay)


def _window_size(window: wechat_detector.WindowInfo) -> tuple[int, int]:
    left, top, right, bottom = window.rect
    return right - left, bottom - top


def _wheel(window: wechat_detector.WindowInfo, delta: int, times: int) -> None:
    width, height = _window_size(window)
    x = int(window.rect[0] + width * 0.2)
    y = int(window.rect[1] + height * 0.55)
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    for _ in range(times):
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, None)
        time.sleep(0.01)


def _compact(text: str) -> str:
    return "".join((text or "").split())


def _avatar_rows(image, min_y: int, max_y: int) -> list[dict]:
    """按头像像素找出可点击的联系人行。

    通讯录里组织名、分组标题只有文字没有头像，OCR 对「名字 + @微信号」
    的彩色排版又识别很差，因此用头像色块定位行，比 OCR 行更稳。
    """
    width, height = image.size
    x0 = max(60, AVATAR_BAND_LEFT)
    x1 = min(AVATAR_BAND_RIGHT, int(width * 0.45))
    if x1 - x0 < 12:
        return []

    pixels = image.load()
    bg_x = max(0, x0 - 8)
    threshold = max(AVATAR_MIN_HITS, (x1 - x0) // 4)
    flags: list[bool] = []
    for y in range(height):
        base_red, base_green, base_blue = pixels[bg_x, y][:3]
        hits = 0
        for x in range(x0, x1):
            red, green, blue = pixels[x, y][:3]
            if (
                abs(red - base_red) + abs(green - base_green) + abs(blue - base_blue)
                > AVATAR_COLOR_DELTA
            ):
                hits += 1
        flags.append(hits >= threshold)

    # 头像上下边缘会因圆角抗锯齿出现几行低命中，先补齐短间隙再分组
    for y in range(1, height):
        if not flags[y]:
            for offset in range(1, AVATAR_GAP_TOLERANCE + 1):
                if y - offset < 0 or y + offset >= height:
                    break
                if flags[y - offset] and flags[y + offset]:
                    flags[y] = True
                    break

    rows: list[dict] = []
    start = None
    for y in range(height):
        active = flags[y]
        if active and start is None:
            start = y
        elif not active and start is not None:
            size = y - start
            if AVATAR_MIN_HEIGHT <= size <= AVATAR_MAX_HEIGHT:
                center = (start + y - 1) // 2
                if min_y <= center <= max_y:
                    rows.append({"y": center, "top": start, "bottom": y - 1})
            start = None
    if start is not None:
        size = height - start
        if AVATAR_MIN_HEIGHT <= size <= AVATAR_MAX_HEIGHT:
            center = (start + height - 1) // 2
            if min_y <= center <= max_y:
                rows.append({"y": center, "top": start, "bottom": height - 1})
    return rows


def _band_max_hits(pixels, band: tuple[int, int], bg_x: int, top: int, bottom: int) -> int:
    """在头像横带里找一段纵向范围内最大的色差命中数。"""
    x0, x1 = band
    best = 0
    for y in range(max(0, top), bottom):
        base = pixels[bg_x, y][:3]
        hits = 0
        for x in range(x0, x1):
            pixel = pixels[x, y][:3]
            delta = (
                abs(pixel[0] - base[0])
                + abs(pixel[1] - base[1])
                + abs(pixel[2] - base[2])
            )
            if delta > AVATAR_COLOR_DELTA:
                hits += 1
        best = max(best, hits)
    return best


def _fill_missing_rows(image, rows: list[dict], min_y: int, max_y: int) -> list[dict]:
    """按行距补回漏识别的人行。

    浅色头像（白猫、白底插画）只有中间一小块和列表底色有差异，色块高度
    不到 AVATAR_MIN_HEIGHT，会被 _avatar_rows 整个丢掉，表现就是列表见底
    时最后几个人被跳过。联系人行行距固定，缺哪几行可以按前后行距推算，
    再确认那个位置确实有内容才补进去。
    """
    if len(rows) < 3:
        return rows
    steps = [b["y"] - a["y"] for a, b in zip(rows, rows[1:]) if 30 <= b["y"] - a["y"] <= 90]
    if not steps:
        return rows
    steps.sort()
    pitch = steps[len(steps) // 2]

    width, _ = image.size
    x0 = max(60, AVATAR_BAND_LEFT)
    x1 = min(AVATAR_BAND_RIGHT, int(width * 0.45))
    bg_x = max(0, x0 - 8)
    need = max(6, (x1 - x0) // 5)
    pixels = image.load()

    result: list[dict] = []
    for current, following in zip(rows, rows[1:]):
        result.append(current)
        missing = round((following["y"] - current["y"]) / pitch) - 1
        for index in range(1, missing + 1):
            y = current["y"] + index * pitch
            if not min_y <= y <= max_y:
                continue
            if _band_max_hits(pixels, (x0, x1), bg_x, y - 4, y + 5) >= need:
                result.append({"y": y, "top": y - 10, "bottom": y + 10})
    result.append(rows[-1])
    return result


def _merge_name_rows(
    rows: list[dict], lines: list[dict], width: int, min_y: int, max_y: int
) -> list[dict]:
    """头像定位漏掉的行用 OCR 名字行补回来。

    浅色头像、整张照片的头像和底色差异小，色块高度不够会被 _avatar_rows 丢掉，
    而名字是黑字，OCR 反而是稳的。这里只认列表正文里的名字行：过滤入口和分组
    标题，并要求落在已有行的行距网格上，避免把「A」这类首字母当联系人。
    """
    if not lines:
        return rows
    ys = [row["y"] for row in rows]
    pitch = 54
    steps = [b - a for a, b in zip(ys, ys[1:]) if 30 <= b - a <= 90]
    if steps:
        steps.sort()
        pitch = steps[len(steps) // 2]

    merged = list(rows)
    for line in lines:
        text = _compact(line.get("text", ""))
        if len(text) < 2 or text in UI_LABELS:
            continue
        # 分组标题右侧的人数是单独一行的纯数字（「企业微信联系人 14」「联系人 179」），
        # 它不是人名，补成行就会点进分组标题把计数当昵称写库
        if text.isdigit():
            continue
        if any(
            wechat_detector.fuzzy_hit(text, hint)
            for hint in ("通讯录管理", "公众号", "企业微信联系人", "星标朋友", "搜索")
        ):
            continue
        left = float(line.get("x") or 0)
        y = int(float(line.get("y") or 0))
        if not 70 <= left <= width * 0.62:
            continue
        if not min_y <= y <= max_y:
            continue
        if ys and min(abs(y - value) for value in ys) <= 24:
            continue
        if ys:
            gap = abs((y - ys[0]) % pitch)
            if min(gap, pitch - gap) > 14:
                continue
        merged.append({"y": y, "top": y - 10, "bottom": y + 10})
    merged.sort(key=lambda row: row["y"])
    unique: list[dict] = []
    for row in merged:
        # 补进来的名字行可能和头像行落在同一行，间隔小于 20px 就并成一行
        if unique and row["y"] - unique[-1]["y"] < 20:
            continue
        unique.append(row)
    return unique


def _list_still(before, after) -> bool:
    """比较滚动前后列表区像素，判断滚轮是否已经推不动（列表见底）。"""
    width = min(before.width, after.width)
    height = min(before.height, after.height)
    box = (70, 100, int(width * 0.33), height - 2)
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    diff = ImageChops.difference(before.crop(box), after.crop(box))
    return max(ImageStat.Stat(diff).mean) < 1.5


class AddressBookCollector:
    """在后台线程里顺序遍历通讯录，采集结果实时写入 SQLite。"""

    def __init__(self, database: WeChatDatabase, adapter: WeChatUiAdapter) -> None:
        self.database = database
        self.adapter = adapter
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._jobs_lock = threading.Lock()
        self._counter = 0

    # ------------------------------------------------------------ 对外接口

    def start(self, max_contacts: int = 1000, settle_seconds: float = 0.6) -> dict:
        if not 1 <= max_contacts <= 3000:
            raise CollectError("max_contacts 必须在 1 到 3000 之间")
        if not 0.3 <= settle_seconds <= 2.0:
            raise CollectError("settle_seconds 必须在 0.3 到 2.0 之间")
        if not AUTOMATION_LOCK.acquire(blocking=False):
            raise AutomationBusyError("已有自动化任务在运行，请等待结束或取消")

        try:
            window = self.adapter.main_window()
            if window is None:
                raise CollectError("未找到微信主窗口")
            # 窗口最小化时截出来是全黑，先还原并放到前台再读取界面文字
            friend_search.force_foreground(window.hwnd)
            time.sleep(0.3)
            window = self._window(window)
            # 换号或停在聊天页时先自动切到通讯录页，避免后面把点击打到聊天窗口里
            self._ensure_wide_layout(window)
            window = self._window(window)
            self._activate_contacts_page(window)
            window = self._window(window)
            lines = self.adapter.read_text(window.hwnd)
            try:
                self._ensure_contacts_page(window, lines)
            except CollectError:
                # 列表滚在中段时「新的朋友/公众号」不在视野，校验会误报；
                # 先滚回顶部再重新校验一次，用户就不用手动滚了
                friend_search.force_foreground(window.hwnd)
                self._scroll_to_top(window)
                self._ensure_contacts_page(window, self.adapter.read_text(window.hwnd))
        except Exception:
            AUTOMATION_LOCK.release()
            raise

        job_id = self._next_job_id()
        job = {
            "job_id": job_id,
            "state": "running",
            "scanned": 0,
            "collected": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "stop_reason": "",
            "max_contacts": max_contacts,
            "settle_seconds": settle_seconds,
            "hwnd": window.hwnd,
            "window_title": window.title,
            "current_nickname": "",
            "current_wechat_id": "",
            "last_error": "",
            "cancel": False,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finished_at": "",
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
            while len(self._jobs) > 20:
                self._jobs.popitem(last=False)

        thread = threading.Thread(
            target=self._run, args=(job, window), name=f"collect-{job_id}", daemon=True
        )
        thread.start()
        return self._snapshot(job)

    def get(self, job_id: str) -> dict:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"采集任务不存在: {job_id}")
            return self._snapshot(job)

    def cancel(self, job_id: str) -> dict:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"采集任务不存在: {job_id}")
            if job["state"] == "running":
                job["cancel"] = True
            return self._snapshot(job)

    # ------------------------------------------------------------ 线程主体

    def _run(self, job: dict, window: wechat_detector.WindowInfo) -> None:
        try:
            friend_search.force_foreground(window.hwnd)
            time.sleep(0.3)
            self._scroll_to_top(window)
            processed: set[str] = set()
            previous_key = ""
            stop_reason = ""
            idle_screens = 0
            screens = 0

            # 按本次识别到的联系人数量停止：重复采集时这些联系人会被更新而不是新增，
            # 只看 created 会一直扫到列表结束
            while len(processed) < job["max_contacts"] and screens < 400:
                if job["cancel"]:
                    job["state"] = "cancelled"
                    stop_reason = "cancelled"
                    break
                # 用户中途拖动过窗口时，坐标会整体平移，按旧位置点会点到窗口外面，
                # 表现就是一直点不动、以为见底了。每屏重新取一次窗口位置。
                fresh = self.adapter.find_window(hwnd=window.hwnd)
                if fresh is not None:
                    window = fresh
                rows = self._contact_rows(window)
                if not rows:
                    idle_screens += 1
                    if idle_screens >= 3:
                        raise CollectError(
                            "联系人列表没有识别到可点击的行，请确认微信停在通讯录页"
                        )
                    self._scroll(window)
                    screens += 1
                    continue

                before = set(processed)
                for row in rows:
                    if job["cancel"] or len(processed) >= job["max_contacts"]:
                        break
                    record, unchanged = self._click_and_read(
                        window, row, job, previous_key
                    )
                    if unchanged:
                        # 点了没换人，这一行多半在列表底部被挡住，留到下一屏再试
                        job["skipped"] += 1
                        continue
                    key = profile_key(record)
                    if not key:
                        continue
                    logger.info(
                        "row y=%s key=%s new=%s", row["y"], key, key not in processed
                    )
                    previous_key = key
                    saved = self.database.save_profile_candidate(record)
                    processed.add(key)
                    job["scanned"] += 1
                    job["collected"] = len(processed)
                    if saved["action"] == "created":
                        job["created"] += 1
                    else:
                        job["updated"] += 1
                    job["current_nickname"] = record.get("nickname") or ""
                    job["current_wechat_id"] = record.get("wechat_id") or ""

                if processed == before:
                    idle_screens += 1
                    if idle_screens < 3:
                        # 这一屏全是熟人，多半是滚轮被吞了没推进，多滚一段再试
                        self._scroll(window, times=18)
                        time.sleep(0.4)
                else:
                    idle_screens = 0
                logger.info(
                    "screen=%s rows=%s new=%s scanned=%s skipped=%s idle=%s",
                    screens,
                    len(rows),
                    len(processed) - len(before),
                    job["scanned"],
                    job["skipped"],
                    idle_screens,
                )
                if idle_screens >= 3:
                    stop_reason = "no_new_contacts"
                    break

                before_scroll = screen_reader.capture_window(window.hwnd)
                self._scroll(window)
                screens += 1
                time.sleep(0.5)
                if _list_still(
                    before_scroll, screen_reader.capture_window(window.hwnd)
                ):
                    # 滚不动有三种可能：真到底了、窗口刚被拖走、截到画面刷新的瞬间。
                    # 连续三次取最新位置重滚都不动，才认定到底
                    for _ in range(2):
                        window = self._window(window)
                        before_scroll = screen_reader.capture_window(window.hwnd)
                        self._scroll(window)
                        time.sleep(0.5)
                        if not _list_still(
                            before_scroll, screen_reader.capture_window(window.hwnd)
                        ):
                            break
                    else:
                        stop_reason = "list_bottom"
                        break

            if job["state"] == "running":
                job["state"] = "completed"
            job["stop_reason"] = stop_reason
        except Exception as exc:  # noqa: BLE001 - 后台任务需要把异常反馈到接口
            logger.exception("通讯录采集失败")
            job["state"] = "failed"
            job["last_error"] = str(exc)
        finally:
            job["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            job["cancel"] = False
            AUTOMATION_LOCK.release()

    # ------------------------------------------------------------ 页面与行定位

    @staticmethod
    def _ensure_wide_layout(window: wechat_detector.WindowInfo) -> None:
        """窗口太窄时微信会收起右侧资料栏，点联系人读不到资料卡。

        采集全程在同一个窗口里读资料卡，所以先把窗口撑到三栏布局的最小尺寸；
        已经是宽窗口就不动它。
        """
        left, top, right, bottom = window.rect
        width, height = right - left, bottom - top
        if width >= LAYOUT_MIN_WIDTH and height >= LAYOUT_MIN_HEIGHT:
            return
        user32.SetWindowPos(
            window.hwnd,
            0,
            left,
            top,
            max(width, LAYOUT_TARGET[0]),
            max(height, LAYOUT_TARGET[1]),
            SWP_NOZORDER_NOACTIVATE,
        )
        time.sleep(0.5)

    def _activate_contacts_page(self, window: wechat_detector.WindowInfo) -> None:
        """确认停在通讯录页并选中一个联系人；停在聊天页时自动点左侧栏切过去。

        换账号或刚聊完天时，微信停在会话窗口，此时直接采集会把点击打到聊天窗口里。
        左侧栏「通讯录」图标位置固定，按头像区下方的几档高度逐个试，切成功即停。
        """
        window = self._window(window)
        rows = self._contact_rows(window)
        if rows and self._has_profile(self.adapter.read_text(window.hwnd)):
            return

        if not rows:
            width, height = _window_size(window)
            if width < 200 or height < 200:
                raise CollectError("微信窗口已最小化，请先把窗口还原")
            # 左侧栏图标是固定像素布局：聊天约 118、通讯录约 172、收藏约 210（以窗口顶为原点），
            # 和窗口高度无关，所以按像素试而不是按比例
            for offset in (172, 168, 176, 164, 180):
                if offset >= height:
                    continue
                friend_sender.click_point(window, 36, offset, self.adapter)
                time.sleep(0.6)
                window = self._window(window)
                rows = self._contact_rows(window)
                if rows:
                    break
            else:
                raise CollectError("切不到微信「通讯录」页，请手动点一下左侧的联系人图标")

        if self._has_profile(self.adapter.read_text(window.hwnd)):
            return
        # 切到通讯录页后右侧还是空的，需要点一行打开资料卡
        for row in rows[:3]:
            friend_sender.click_point(
                window, int(_window_size(window)[0] * 0.16), row["y"], self.adapter
            )
            time.sleep(0.7)
            window = self._window(window)
            if self._has_profile(self.adapter.read_text(window.hwnd)):
                return

    @staticmethod
    def _has_profile(lines: list[dict]) -> bool:
        return any(
            wechat_detector.fuzzy_hit(line.get("text", ""), hint)
            for line in lines
            for hint in ("朋友资料", "微信号", "徵信号")
        )

    @staticmethod
    def _ensure_contacts_page(
        window: wechat_detector.WindowInfo, lines: list[dict]
    ) -> None:
        joined = "".join((line.get("text") or "") for line in lines)
        if wechat_detector.is_login_page_text(joined):
            raise CollectError("微信停在登录页，无法采集通讯录")
        has_manage = any(
            wechat_detector.fuzzy_hit(line.get("text", ""), "通讯录管理") for line in lines
        )
        has_contacts = any(
            wechat_detector.fuzzy_hit(line.get("text", ""), "联系人") for line in lines
        ) or (
            any(
                wechat_detector.fuzzy_hit(line.get("text", ""), "新的朋友")
                for line in lines
            )
            and any(
                wechat_detector.fuzzy_hit(line.get("text", ""), "公众号")
                for line in lines
            )
        )
        has_profile = any(
            wechat_detector.fuzzy_hit(line.get("text", ""), hint)
            for line in lines
            for hint in ("朋友资料", "微信号", "徵信号")
        )
        if not ((has_manage or has_contacts) and has_profile):
            raise CollectError("请先把微信切到「通讯录」页，并选中一个联系人")

    def _contact_rows(self, window: wechat_detector.WindowInfo) -> list[dict]:
        """返回当前可见的联系人行（含中心点），跳过仅作分组标题的文字行。"""
        image = screen_reader.capture_window(window.hwnd)
        lines = screen_reader.ocr_image(image)
        min_y = self._list_top(lines)
        rows = _avatar_rows(image, min_y, image.height - 4)
        rows = _fill_missing_rows(image, rows, min_y, image.height - 4)
        return _merge_name_rows(rows, lines, image.width, min_y, image.height - 4)

    @staticmethod
    def _list_top(lines: list[dict]) -> int:
        """列表正文起点，取「联系人」分组标题之下。

        「联系人」这一组才是要采的人。公众号服务号、企业微信联系人这两组即使被
        展开，它们的行也都在「联系人」标题之上，所以下限必须用「联系人」标题算，
        不能拿上面那两行当锚点，否则会把企业号和分组计数（如「14」「179」）当成
        联系人写进库。
        """
        header = next(
            (
                line
                for line in lines
                if re.fullmatch(r"[>〉v∨]?联系人", _compact(line.get("text", "")))
            ),
            None,
        )
        if header is not None:
            # 标题行本身约 40px 高，紧跟其下的几像素仍是标题区，留够距离再算正文
            return int(float(header.get("y") or 0)) + 40
        special = [
            line
            for line in lines
            if wechat_detector.fuzzy_hit(line.get("text", ""), "公众号")
            or wechat_detector.fuzzy_hit(line.get("text", ""), "企业微信联系人")
        ]
        if special:
            return int(max(float(line.get("y") or 0) for line in special)) + 25
        # 分组入口滚出视野时没有锚点，取搜索框下方的固定位置，避免点到顶部入口
        return 110

    def _click_and_read(
        self,
        window: wechat_detector.WindowInfo,
        row: dict,
        job: dict,
        previous_key: str,
    ) -> tuple[dict, bool]:
        """点击一行并读取资料页。

        返回 (record, unchanged)。点击后资料卡仍是上一个人（底部浅色头像行、
        被列表边缘挡住的行都可能点了没反应）时，换两种位置并多等几拍重试；
        全部失败就标记 unchanged，让调用方跳过这一行——否则会把上一个人的
        资料再存一遍，被跳过的联系人从此查不到。
        """
        width, _ = _window_size(window)
        wait = max(float(job["settle_seconds"]), 0.5)
        record: dict = {}
        # 点击落点取头像带中心再往右一点，窄窗口按宽度百分比算会落到列表左侧空白上
        for index, offset_x in enumerate((AVATAR_BAND_LEFT + 24, AVATAR_BAND_LEFT + 40)):
            if index:
                # 第一遍点完没反应，可能就是窗口刚被拖走了，重取位置再试
                window = self._window(window)
            if offset_x >= width - 20:
                offset_x = max(AVATAR_BAND_LEFT + 4, width // 3)
            offset = 0 if index == 0 else 8
            friend_sender.click_point(
                window, offset_x, int(row["y"]) + offset, self.adapter
            )
            for _ in range(3):
                time.sleep(wait)
                record = self._read_profile(window)
                key = profile_key(record)
                if key and key != previous_key:
                    return record, False
        return record, True

    def _window(self, window: wechat_detector.WindowInfo) -> wechat_detector.WindowInfo:
        """取一次最新窗口位置；窗口被拖动过时坐标会整体平移，按旧位置点会点空。"""
        fresh = self.adapter.find_window(hwnd=window.hwnd)
        return fresh or window

    def _scroll(self, window: wechat_detector.WindowInfo, times: int = 9) -> None:
        # 实测一格滚轮约 25px、联系人行约 54px，9 次（18 格）推进约 8 行，
        # 视野内保留 1~2 行的重叠，既不漏人也不会每屏只前进一个人
        _wheel(self._window(window), -240, times)

    def _scroll_to_top(self, window: wechat_detector.WindowInfo) -> None:
        """先把焦点放到左侧列表并回到顶部，避免从中间开始采集。"""
        window = self._window(window)
        width, height = _window_size(window)
        x = int(window.rect[0] + width * 0.2)
        y = int(window.rect[1] + height * 0.45)
        user32.SetCursorPos(x, y)
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, None)
        user32.mouse_event(0x0004, 0, 0, 0, None)
        time.sleep(0.15)
        # 一格滚轮只走二十几像素，从列表末尾倒回顶部要滚上千次，必须先用 Home 跳。
        # Home 只对拿到键盘焦点的列表生效，所以上面先点一下列表。
        for _ in range(6):
            _press_key(0x24)  # VK_HOME
            time.sleep(0.35)
            window = self._window(window)
            if self._at_list_top(window):
                return
            _wheel(window, 120, 1500)
            time.sleep(0.3)

    def _at_list_top(self, window: wechat_detector.WindowInfo) -> bool:
        """只 OCR 列表顶部一小块，判断是否真的回到了通讯录最上方。"""
        width, height = _window_size(window)
        if width < 200 or height < 200:
            # 窗口被最小化时 rect 是 -32000，裁切会直接抛坐标错误
            return False
        image = screen_reader.capture_window(window.hwnd)
        top = image.crop((70, 60, int(width * 0.33), min(height, 260)))
        lines = screen_reader.ocr_image(top, scale=3)
        return any(wechat_detector.fuzzy_hit(line.get("text", ""), "新的朋友") for line in lines)

    def _read_profile(self, window: wechat_detector.WindowInfo) -> dict:
        """只 OCR 右侧资料区。

        整窗 OCR 会把左栏列表行和资料行合并成同一行（行左边界落在列表里），
        备注、地区等字段就会被列表文字污染，所以这里直接裁掉列表再识别。
        """
        width, _ = _window_size(window)
        # 列表栏宽度有上限（约 520px），资料栏起点固定在 552 左右。按宽度百分比裁，
        # 900 宽时只裁到 315，列表里的「联系人 179」这类文字会跟着进 OCR，
        # 被当成昵称写库
        crop_x = min(int(width * 0.62), 552)
        image = screen_reader.capture_window(window.hwnd)
        cropped = image.crop((crop_x, 0, image.width, image.height))
        profile_lines = self._ocr_profile(cropped, crop_x, scale=2)
        record = parse_profile(profile_lines, hwnd=window.hwnd, window_title=window.title)
        extra_lines: list[dict] = []

        if not record["wechat_id"]:
            high_lines = self._ocr_profile(cropped, crop_x, scale=3)
            extra_lines = high_lines
            high_record = parse_profile(
                high_lines, hwnd=window.hwnd, window_title=window.title
            )
            if high_record["wechat_id"]:
                if record["nickname"]:
                    high_record["nickname"] = record["nickname"]
                if record["phone"] and not high_record["phone"]:
                    high_record["phone"] = record["phone"]
                    self._set_field(high_record, "phone", record["phone"], "medium")
                record = high_record
            elif high_record["phone"] and not record["phone"]:
                record["phone"] = high_record["phone"]

        recovered = recover_wechat_id(
            record["wechat_id"], profile_lines + extra_lines
        )
        if recovered != record["wechat_id"]:
            record["wechat_id"] = recovered
            self._set_field(record, "wechat_id", recovered, "high")

        if not record["wechat_id"]:
            # 全屏或大窗口时整块 OCR 会把微信号那行小字认错（zuy2310 认成「一 202310」），
            # 单独裁这一行放大再读一次
            reread = self._reread_id(cropped, profile_lines, crop_x)
            if reread:
                record["wechat_id"] = reread
                self._set_field(record, "wechat_id", reread, "medium")
        return record

    @staticmethod
    def _reread_id(cropped, lines: list[dict], crop_x: int) -> str:
        for line in lines:
            if not wechat_detector.fuzzy_hit(line.get("text", ""), "信号"):
                continue
            left = int(float(line.get("x") or 0)) - crop_x
            top = int(float(line.get("y") or 0))
            width = int(float(line.get("width") or 240))
            height = int(float(line.get("height") or 24))
            box = (
                max(0, left - 8),
                max(0, top - 8),
                min(cropped.width, left + width + 60),
                min(cropped.height, top + height + 8),
            )
            if box[2] - box[0] < 20 or box[3] - box[1] < 12:
                continue
            piece = cropped.crop(box)
            text = "".join(
                item.get("text", "") for item in screen_reader.ocr_image(piece, scale=4)
            )
            record = parse_profile([{"text": text, "x": 0, "y": 0}])
            if record["wechat_id"]:
                return record["wechat_id"]
        return ""

    @staticmethod
    def _ocr_profile(cropped, crop_x: int, scale: int) -> list[dict]:
        lines = screen_reader.ocr_image(cropped, scale=scale)
        for line in lines:
            line["x"] = int(line.get("x") or 0) + crop_x
        return lines

    @staticmethod
    def _set_field(record: dict, key: str, value: str, confidence: str) -> None:
        try:
            fields = json.loads(record.get("fields_json") or "{}")
        except json.JSONDecodeError:
            fields = {}
        field = fields.setdefault(key, {})
        field["value"] = value
        field["confidence"] = confidence
        record["fields_json"] = json.dumps(fields, ensure_ascii=False)

    # ------------------------------------------------------------ 辅助

    def _next_job_id(self) -> str:
        self._counter += 1
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"C{stamp}-{self._counter}"

    @staticmethod
    def _snapshot(job: dict) -> dict:
        return {key: value for key, value in job.items() if key != "cancel"}
