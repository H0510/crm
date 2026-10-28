import pytest
from fastapi.testclient import TestClient

from wechat_service.main import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_contact_upsert_list_and_duplicates(client):
    payload = {
        "crm_contact_id": "C10001",
        "name": "张先生",
        "phone": "13800138000",
        "wechat_id": "zhang_wx",
    }
    response = client.post("/api/contacts", json=payload)
    assert response.status_code == 200
    assert response.json()["contact"]["crm_contact_id"] == "C10001"

    duplicate = dict(payload, crm_contact_id="C10002", name="同名客户")
    response = client.post("/api/contacts", json=duplicate)
    assert response.status_code == 200
    assert len(response.json()["duplicates"]) == 1

    response = client.get("/api/contacts")
    assert len(response.json()["contacts"]) == 2

    response = client.get("/api/contacts/C404")
    assert response.status_code == 404


def test_match_without_candidates_returns_unmatched(client):
    client.post(
        "/api/contacts",
        json={"crm_contact_id": "C1", "name": "张先生", "phone": "13800138000"},
    )
    response = client.post("/api/contacts/C1/match")
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["status"] == "unmatched"
    assert body["contact"]["match_status"] == "unmatched"


def test_task_lifecycle_via_api(client):
    client.post(
        "/api/contacts",
        json={"crm_contact_id": "C1", "name": "张先生", "phone": "13800138000"},
    )

    response = client.post("/api/tasks", json={"crm_contact_id": "C1"})
    assert response.status_code == 200
    task_id = response.json()["task"]["id"]

    steps = [
        {"to_status": "reviewing"},
        {"to_status": "pending_confirmation"},
    ]
    for step in steps:
        step.update({"actor": "user", "confirmed": False})
        response = client.post(f"/api/tasks/{task_id}/transition", json=step)
        assert response.status_code == 200, response.text

    response = client.post(
        f"/api/tasks/{task_id}/transition",
        json={"to_status": "submitted", "actor": "system", "confirmed": True},
    )
    assert response.status_code == 400

    response = client.post(
        f"/api/tasks/{task_id}/transition",
        json={"to_status": "submitted", "actor": "user", "confirmed": True},
    )
    assert response.status_code == 200
    assert response.json()["task"]["status"] == "submitted"

    response = client.get(f"/api/tasks/{task_id}/events")
    assert response.status_code == 200
    assert len(response.json()["events"]) == 4


def test_task_for_unknown_contact_returns_400(client):
    response = client.post("/api/tasks", json={"crm_contact_id": "C404"})
    assert response.status_code == 400


def test_meta_statuses(client):
    response = client.get("/api/meta/statuses")
    assert response.status_code == 200
    body = response.json()
    assert "submitted" in body["task_statuses"]
    assert body["task_transitions"]["accepted"] == []
    assert "submitted" in body["manual_confirm_required"]
