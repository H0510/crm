import pytest

from shared import wechat_types as types
from wechat_service import task_manager
from wechat_service.database import WeChatDatabase


@pytest.fixture()
def db(tmp_path):
    database = WeChatDatabase(tmp_path / "test.db")
    database.upsert_contact(
        {
            "crm_contact_id": "C10001",
            "name": "张先生",
            "phone": "13800138000",
            "wechat_id": "zhang_wx",
        }
    )
    return database


def test_create_task_sets_pending_and_audit(db):
    task = task_manager.create_task(db, "C10001")
    assert task["status"] == types.TASK_PENDING
    assert task["search_key"] == "13800138000"

    events = db.list_task_events(task["id"])
    assert len(events) == 1
    assert events[0]["to_status"] == types.TASK_PENDING

    contact = db.get_contact("C10001")
    assert contact["friend_status"] == types.FRIEND_STATUS_PENDING_REVIEW


def test_create_task_requires_existing_contact(db):
    with pytest.raises(task_manager.TaskTransitionError):
        task_manager.create_task(db, "C404")


def test_happy_path_transitions(db):
    task = task_manager.create_task(db, "C10001")
    task = task_manager.transition(db, task["id"], types.TASK_REVIEWING, note="核对搜索结果")
    assert task["status"] == types.TASK_REVIEWING

    task = task_manager.transition(
        db, task["id"], types.TASK_PENDING_CONFIRMATION, note="待用户确认"
    )
    assert task["status"] == types.TASK_PENDING_CONFIRMATION

    task = task_manager.transition(
        db,
        task["id"],
        types.TASK_SUBMITTED,
        actor=types.ACTOR_USER,
        confirmed=True,
        note="用户确认发送",
    )
    assert task["status"] == types.TASK_SUBMITTED
    assert db.get_contact("C10001")["friend_status"] == types.FRIEND_STATUS_SUBMITTED

    task = task_manager.transition(db, task["id"], types.TASK_ACCEPTED, note="对方已通过")
    assert task["status"] == types.TASK_ACCEPTED
    assert db.get_contact("C10001")["friend_status"] == types.FRIEND_STATUS_ACCEPTED

    events = db.list_task_events(task["id"])
    assert [event["to_status"] for event in events] == [
        types.TASK_PENDING,
        types.TASK_REVIEWING,
        types.TASK_PENDING_CONFIRMATION,
        types.TASK_SUBMITTED,
        types.TASK_ACCEPTED,
    ]


def test_submit_requires_user_confirmation(db):
    task = task_manager.create_task(db, "C10001")
    task_manager.transition(db, task["id"], types.TASK_REVIEWING)
    task_manager.transition(db, task["id"], types.TASK_PENDING_CONFIRMATION)

    with pytest.raises(task_manager.TaskTransitionError):
        task_manager.transition(db, task["id"], types.TASK_SUBMITTED, actor=types.ACTOR_SYSTEM)

    with pytest.raises(task_manager.TaskTransitionError):
        task_manager.transition(
            db, task["id"], types.TASK_SUBMITTED, actor=types.ACTOR_USER, confirmed=False
        )


def test_invalid_transition_rejected(db):
    task = task_manager.create_task(db, "C10001")
    with pytest.raises(task_manager.TaskTransitionError):
        task_manager.transition(db, task["id"], types.TASK_ACCEPTED)


def test_unknown_status_rejected(db):
    task = task_manager.create_task(db, "C10001")
    with pytest.raises(task_manager.TaskTransitionError):
        task_manager.transition(db, task["id"], "auto_sent")


def test_cancelled_is_terminal(db):
    task = task_manager.create_task(db, "C10001")
    task_manager.transition(db, task["id"], types.TASK_CANCELLED, note="用户取消")
    with pytest.raises(task_manager.TaskTransitionError):
        task_manager.transition(db, task["id"], types.TASK_REVIEWING)
