"""微信桌面自动化模块的共享类型与常量。

Electron 侧对应 `electron/src/pages/WeChatManager.vue` 里同名的状态常量，
两边保持一致；Python 侧以此为准。
"""

from __future__ import annotations

from typing import Literal, TypedDict


# ---------------------------------------------------------------- 状态常量

LOGIN_STATE_LOGGED_IN = "logged_in"
LOGIN_STATE_NOT_LOGGED_IN = "not_logged_in"
LOGIN_STATE_UNKNOWN = "unknown"
LoginState = Literal["logged_in", "not_logged_in", "unknown"]

MATCH_STATUS_MATCHED = "matched"
MATCH_STATUS_MULTIPLE = "multiple_matches"
MATCH_STATUS_UNMATCHED = "unmatched"
MatchStatus = Literal["matched", "multiple_matches", "unmatched"]

TASK_PENDING = "pending"
TASK_REVIEWING = "reviewing"
TASK_PENDING_CONFIRMATION = "pending_confirmation"
TASK_SUBMITTED = "submitted"
TASK_ACCEPTED = "accepted"
TASK_NOT_FOUND = "not_found"
TASK_AMBIGUOUS = "ambiguous"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

TaskStatus = Literal[
    "pending",
    "reviewing",
    "pending_confirmation",
    "submitted",
    "accepted",
    "not_found",
    "ambiguous",
    "failed",
    "cancelled",
]

TASK_STATUSES: tuple[str, ...] = (
    TASK_PENDING,
    TASK_REVIEWING,
    TASK_PENDING_CONFIRMATION,
    TASK_SUBMITTED,
    TASK_ACCEPTED,
    TASK_NOT_FOUND,
    TASK_AMBIGUOUS,
    TASK_FAILED,
    TASK_CANCELLED,
)

FRIEND_STATUS_NONE = "none"
FRIEND_STATUS_PENDING_REVIEW = "pending_review"
FRIEND_STATUS_SUBMITTED = "submitted"
FRIEND_STATUS_ACCEPTED = "accepted"

ACTOR_USER = "user"
ACTOR_SYSTEM = "system"

# 搜索结果核验结论
SEARCH_OUTCOME_MATCHED = "matched"
SEARCH_OUTCOME_NOT_FOUND = "not_found"
SEARCH_OUTCOME_AMBIGUOUS = "ambiguous"
SEARCH_OUTCOME_NEEDS_REVIEW = "needs_review"

SEARCH_OUTCOMES: tuple[str, ...] = (
    SEARCH_OUTCOME_MATCHED,
    SEARCH_OUTCOME_NOT_FOUND,
    SEARCH_OUTCOME_AMBIGUOUS,
    SEARCH_OUTCOME_NEEDS_REVIEW,
)


# ---------------------------------------------------------------- 数据结构


class WindowInfoDict(TypedDict):
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool
    minimized: bool
    rect: tuple[int, int, int, int]


class OcrLineDict(TypedDict):
    text: str
    x: int
    y: int
    width: int
    height: int


class ContactCandidateDict(TypedDict):
    hwnd: int
    window_title: str
    raw_text: str
    nickname: str
    phone: str
    wechat_id: str
    confidence: str
    x: int
    y: int
    width: int
    height: int
    scanned_at: str


class WeChatStatusDict(TypedDict):
    running: bool
    pids: list[int]
    windows: list[WindowInfoDict]
    main_window: WindowInfoDict | None
    login_state: LoginState
    account: str | None
    evidence: list[str]
    ocr_error: str | None
    checked_at: str


class SearchVerificationDict(TypedDict):
    outcome: str
    matched_by: list[str]
    reason: str
    search_key: str
    hwnd: int
    window_title: str
    checked_at: str
    entries: list[dict]
    lines: list[str]
    not_found_hint: bool
