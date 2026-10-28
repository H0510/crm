"""好友申请任务的状态流转。

设计前提：好友申请是人工确认的单条工作流，不做批量自动发送。
进入「已提交待确认」必须由 user 显式确认，其他状态流转也会写入审计记录。
"""

from __future__ import annotations

import logging

from shared import wechat_types as types

from .database import WeChatDatabase

logger = logging.getLogger(__name__)


class TaskTransitionError(ValueError):
    """非法的任务流转。"""


# 允许的状态流转；不在表里的组合一律拒绝
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    types.TASK_PENDING: {types.TASK_REVIEWING, types.TASK_CANCELLED},
    types.TASK_REVIEWING: {
        types.TASK_PENDING_CONFIRMATION,
        types.TASK_NOT_FOUND,
        types.TASK_AMBIGUOUS,
        types.TASK_FAILED,
        types.TASK_CANCELLED,
    },
    types.TASK_PENDING_CONFIRMATION: {
        types.TASK_SUBMITTED,
        types.TASK_FAILED,
        types.TASK_CANCELLED,
    },
    types.TASK_SUBMITTED: {types.TASK_ACCEPTED, types.TASK_FAILED, types.TASK_CANCELLED},
    types.TASK_ACCEPTED: set(),
    types.TASK_NOT_FOUND: {types.TASK_REVIEWING, types.TASK_CANCELLED},
    types.TASK_AMBIGUOUS: {types.TASK_REVIEWING, types.TASK_FAILED, types.TASK_CANCELLED},
    types.TASK_FAILED: {types.TASK_REVIEWING, types.TASK_CANCELLED},
    types.TASK_CANCELLED: set(),
}

# 这些流转必须由用户显式确认
MANUAL_CONFIRM_REQUIRED = {types.TASK_SUBMITTED}

# 任务状态与 CRM 联系人好友状态的对应关系
FRIEND_STATUS_MAP = {
    types.TASK_PENDING: types.FRIEND_STATUS_PENDING_REVIEW,
    types.TASK_REVIEWING: types.FRIEND_STATUS_PENDING_REVIEW,
    types.TASK_PENDING_CONFIRMATION: types.FRIEND_STATUS_PENDING_REVIEW,
    types.TASK_SUBMITTED: types.FRIEND_STATUS_SUBMITTED,
    types.TASK_ACCEPTED: types.FRIEND_STATUS_ACCEPTED,
}


def create_task(
    db: WeChatDatabase,
    crm_contact_id: str,
    search_key: str = "",
    evidence: dict | None = None,
) -> dict:
    """为单个 CRM 联系人创建好友申请任务。

    刻意不提供批量创建：自动连续发送陌生好友申请会触发平台风控，
    也会绕过用户确认环节。
    """
    contact = db.get_contact(crm_contact_id)
    if contact is None:
        raise TaskTransitionError(f"CRM 联系人不存在: {crm_contact_id}")
    if not search_key:
        search_key = contact.get("phone") or contact.get("wechat_id") or ""

    task = db.create_task(
        crm_contact_id=crm_contact_id,
        status=types.TASK_PENDING,
        search_key=search_key,
        evidence=evidence,
    )
    db.add_task_event(
        task_id=task["id"],
        from_status="",
        to_status=types.TASK_PENDING,
        actor=types.ACTOR_USER,
        note="创建好友申请任务",
    )
    db.update_friend_status(crm_contact_id, types.FRIEND_STATUS_PENDING_REVIEW)
    return task


def transition(
    db: WeChatDatabase,
    task_id: int,
    to_status: str,
    actor: str = types.ACTOR_SYSTEM,
    note: str = "",
    confirmed: bool = False,
) -> dict:
    """执行一次状态流转，非法流转直接报错。"""
    if to_status not in types.TASK_STATUSES:
        raise TaskTransitionError(f"未知任务状态: {to_status}")
    if actor not in (types.ACTOR_USER, types.ACTOR_SYSTEM):
        raise TaskTransitionError(f"未知操作者: {actor}")

    task = db.get_task(task_id)
    if task is None:
        raise TaskTransitionError(f"任务不存在: {task_id}")

    current = task["status"]
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if to_status not in allowed:
        raise TaskTransitionError(f"不允许从 {current} 流转到 {to_status}")

    if to_status in MANUAL_CONFIRM_REQUIRED:
        if actor != types.ACTOR_USER or not confirmed:
            raise TaskTransitionError("提交好友申请必须由用户显式确认")

    last_error = "" if to_status != types.TASK_FAILED else note
    updated = db.update_task_status(task_id, to_status, last_error=last_error)
    db.add_task_event(
        task_id=task_id,
        from_status=current,
        to_status=to_status,
        actor=actor,
        note=note,
    )

    friend_status = FRIEND_STATUS_MAP.get(to_status)
    if friend_status:
        try:
            db.update_friend_status(task["crm_contact_id"], friend_status)
        except KeyError:
            logger.warning("任务对应的 CRM 联系人已不存在：%s", task["crm_contact_id"])

    updated["previous_status"] = current
    return updated


def list_tasks(db: WeChatDatabase, status: str | None = None) -> list[dict]:
    return db.list_tasks(status=status)


def task_events(db: WeChatDatabase, task_id: int) -> list[dict]:
    return db.list_task_events(task_id)
