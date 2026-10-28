import csv
import io
import json

from fastapi.testclient import TestClient

from wechat_service.database import WeChatDatabase
from wechat_service.main import create_app


def profile(**overrides) -> dict:
    record = {
        "hwnd": 1,
        "window_title": "微信",
        "raw_text": "微信号：ABC123 电话：15576481018",
        "nickname": "见录",
        "phone": "15576481018",
        "wechat_id": "ABC123",
        "remark": "2.22鑫宝",
        "region": "上海",
        "email": "",
        "source": "address_book",
        "confidence": "high",
        "fields_json": json.dumps(
            {"phone": {"value": "15576481018", "confidence": "high", "raw": "15576481018"}},
            ensure_ascii=False,
        ),
    }
    record.update(overrides)
    return record


def test_save_profile_candidate_deduplicates_by_wechat_id(tmp_path):
    database = WeChatDatabase(tmp_path / "collect.db")
    first = database.save_profile_candidate(profile())
    second = database.save_profile_candidate(profile(remark="新备注"))
    assert first["action"] == "created"
    assert second["action"] == "updated"
    assert first["id"] == second["id"]

    rows = database.list_candidates()
    assert len(rows) == 1
    assert rows[0]["remark"] == "新备注"
    assert rows[0]["region"] == "上海"


def test_save_profile_candidate_without_wechat_id_deduplicates_by_nickname_phone(tmp_path):
    database = WeChatDatabase(tmp_path / "collect.db")
    database.save_profile_candidate(profile(wechat_id=""))
    database.save_profile_candidate(profile(wechat_id=""))
    assert len(database.list_candidates()) == 1


def test_export_csv_contains_collected_fields(tmp_path):
    app = create_app(tmp_path / "export.db")
    app.state.database.save_profile_candidate(profile())
    with TestClient(app) as client:
        response = client.get("/api/candidates/export.csv")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "微信号"
    assert rows[1][0] == "ABC123"
    assert rows[1][1] == "15576481018"
    assert rows[1][3] == "high"
