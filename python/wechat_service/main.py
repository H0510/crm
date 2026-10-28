"""FastAPI 本地服务入口。

给 Electron 桌面端提供微信状态、界面读取、联系人匹配、好友申请任务四类能力。
服务只跑在本地，界面读取全部为只读操作。
"""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shared import wechat_types as types

from . import (
    address_book_collector,
    contact_matcher,
    contact_reader,
    friend_search,
    friend_sender,
    task_manager,
    wechat_detector,
)
from .database import DEFAULT_DB_PATH, WeChatDatabase
from .ui_adapter import WeChatUiAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class ContactPayload(BaseModel):
    crm_contact_id: str = Field(min_length=1, description="CRM 客户编号")
    name: str = ""
    phone: str = ""
    wechat_id: str = ""
    wechat_nickname: str = ""
    wechat_remark: str = ""


class TaskPayload(BaseModel):
    crm_contact_id: str = Field(min_length=1)
    search_key: str = ""
    evidence: dict = Field(default_factory=dict)


class TransitionPayload(BaseModel):
    to_status: str
    actor: str = types.ACTOR_USER
    note: str = ""
    confirmed: bool = False


class ScanPayload(BaseModel):
    hwnds: list[int] | None = None


class ProbePayload(BaseModel):
    hwnd: int | None = None


class SearchPayload(BaseModel):
    auto_input: bool = Field(
        default=False, description="是否由程序发送一次 Ctrl+V"
    )
    confirmed: bool = Field(default=False, description="自动输入必须由用户显式确认")
    copy_to_clipboard: bool = Field(default=True, description="是否把搜索词放进剪贴板")


class SendPayload(BaseModel):
    confirmed: bool = Field(default=False, description="点击发送必须由用户显式确认")


class CollectPayload(BaseModel):
    max_contacts: int = Field(default=1000, ge=1, le=3000)
    settle_seconds: float = Field(default=0.6, ge=0.3, le=2.0)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    database = WeChatDatabase(db_path or DEFAULT_DB_PATH)
    adapter = WeChatUiAdapter()
    collector = address_book_collector.AddressBookCollector(database, adapter)

    app = FastAPI(
        title="GeoAI 微信桌面自动化本地服务",
        version="0.1.0",
        description="只读的微信界面读取 + CRM 联系人匹配 + 好友申请任务管理",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.database = database
    app.state.adapter = adapter
    app.state.collector = collector

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": "0.1.0",
            "db_path": str(database.db_path),
        }

    # ------------------------------------------------------------ 微信状态

    @app.get("/api/wechat/status")
    def wechat_status(include_ocr: bool = True) -> dict:
        status = wechat_detector.detect(include_ocr=include_ocr)
        return status.to_dict()

    @app.get("/api/wechat/windows")
    def wechat_windows() -> dict:
        windows = adapter.list_windows()
        main_window = wechat_detector.pick_main_window(windows)
        return {
            "windows": [window.to_dict() for window in windows],
            "main_window": main_window.to_dict() if main_window else None,
        }

    @app.get("/api/wechat/windows/{hwnd}/screenshot")
    def window_screenshot(hwnd: int) -> Response:
        try:
            content = adapter.screenshot(hwnd)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(content=content, media_type="image/png")

    @app.post("/api/wechat/windows/{hwnd}/ocr")
    def window_ocr(hwnd: int) -> dict:
        try:
            lines = adapter.read_text(hwnd)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"hwnd": hwnd, "line_count": len(lines), "lines": lines}

    @app.post("/api/wechat/probe")
    def wechat_probe(payload: ProbePayload | None = None) -> dict:
        hwnd = payload.hwnd if payload else None
        if hwnd is None:
            main_window = adapter.main_window()
            if main_window is None:
                raise HTTPException(status_code=404, detail="未找到微信界面窗口")
            hwnd = main_window.hwnd
        report = adapter.probe(hwnd)
        return report

    @app.get("/api/wechat/add-friend-window")
    def add_friend_window() -> dict:
        window = friend_search.find_add_friend_window(adapter)
        return {
            "found": window is not None,
            "window": window.to_dict() if window else None,
        }

    # ------------------------------------------------------------ 联系人

    @app.get("/api/contacts")
    def list_contacts(match_status: str | None = None) -> dict:
        return {"contacts": database.list_contacts(match_status=match_status)}

    @app.post("/api/contacts")
    def upsert_contact(payload: ContactPayload) -> dict:
        duplicates = contact_matcher.find_duplicates(
            database.list_contacts(), payload.model_dump()
        )
        contact = database.upsert_contact(payload.model_dump())
        return {"contact": contact, "duplicates": duplicates}

    @app.get("/api/contacts/{crm_contact_id}")
    def get_contact(crm_contact_id: str) -> dict:
        contact = database.get_contact(crm_contact_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="联系人不存在")
        return {"contact": contact}

    @app.post("/api/contacts/scan")
    def scan_contacts(payload: ScanPayload | None = None) -> dict:
        try:
            result = contact_reader.scan_contacts(
                adapter=adapter, hwnds=payload.hwnds if payload else None
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        database.save_candidates(result["candidates"])
        return result

    @app.get("/api/candidates")
    def list_candidates() -> dict:
        return {"candidates": database.list_candidates()}

    @app.get("/api/candidates/export.csv")
    def export_candidates() -> Response:
        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "微信号",
                "电话",
                "电话原始值",
                "电话置信度",
                "昵称",
                "备注",
                "地区",
                "邮箱",
                "来源",
                "置信度",
                "采集时间",
            ]
        )
        for row in database.list_candidates():
            try:
                fields = json.loads(row.get("fields_json") or "{}")
            except json.JSONDecodeError:
                fields = {}
            phone_field = fields.get("phone") or {}
            writer.writerow(
                [
                    row.get("wechat_id") or "",
                    row.get("phone") or "",
                    phone_field.get("raw") or "",
                    phone_field.get("confidence") or "",
                    row.get("nickname") or "",
                    row.get("remark") or "",
                    row.get("region") or "",
                    row.get("email") or "",
                    row.get("source") or "",
                    row.get("confidence") or "",
                    row.get("scanned_at") or "",
                ]
            )
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="wechat_contacts.csv"',
            },
        )

    # ------------------------------------------------------------ 通讯录采集

    @app.post("/api/collect/address-book")
    def start_address_book_collect(payload: CollectPayload | None = None) -> dict:
        data = payload or CollectPayload()
        try:
            job = collector.start(
                max_contacts=data.max_contacts,
                settle_seconds=data.settle_seconds,
            )
        except address_book_collector.AutomationBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job": job}

    @app.get("/api/collect/jobs/{job_id}")
    def get_collect_job(job_id: str) -> dict:
        try:
            return {"job": collector.get(job_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/collect/jobs/{job_id}/cancel")
    def cancel_collect_job(job_id: str) -> dict:
        try:
            return {"job": collector.cancel(job_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/contacts/{crm_contact_id}/match")
    def match_contact(crm_contact_id: str) -> dict:
        contact = database.get_contact(crm_contact_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="联系人不存在")

        candidates = database.list_candidates()
        result = contact_matcher.match_contact(contact, candidates)
        database.update_match_status(
            crm_contact_id,
            result["status"],
            result.get("reason", ""),
        )
        return {"result": result, "contact": database.get_contact(crm_contact_id)}

    # ------------------------------------------------------------ 任务

    @app.get("/api/tasks")
    def list_tasks(status: str | None = None) -> dict:
        return {"tasks": task_manager.list_tasks(database, status=status)}

    @app.post("/api/tasks")
    def create_task(payload: TaskPayload) -> dict:
        try:
            task = task_manager.create_task(
                database,
                crm_contact_id=payload.crm_contact_id,
                search_key=payload.search_key,
                evidence=payload.evidence,
            )
        except task_manager.TaskTransitionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int) -> dict:
        task = database.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"task": task}

    @app.post("/api/tasks/{task_id}/transition")
    def transition_task(task_id: int, payload: TransitionPayload) -> dict:
        try:
            task = task_manager.transition(
                database,
                task_id=task_id,
                to_status=payload.to_status,
                actor=payload.actor,
                note=payload.note,
                confirmed=payload.confirmed,
            )
        except task_manager.TaskTransitionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task}

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: int) -> dict:
        if database.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"events": task_manager.task_events(database, task_id)}

    @app.post("/api/tasks/{task_id}/search")
    def search_task(task_id: int, payload: SearchPayload | None = None) -> dict:
        payload = payload or SearchPayload()
        try:
            return friend_search.verify_task(
                database,
                task_id=task_id,
                adapter=adapter,
                auto_input=payload.auto_input,
                confirmed=payload.confirmed,
                copy_to_clipboard=payload.copy_to_clipboard,
            )
        except friend_search.SearchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/send")
    def send_task(task_id: int, payload: SendPayload | None = None) -> dict:
        payload = payload or SendPayload()
        try:
            return friend_sender.send_friend_request(
                database,
                task_id=task_id,
                adapter=adapter,
                confirmed=payload.confirmed,
            )
        except friend_sender.SendError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------ 元信息

    @app.get("/api/meta/statuses")
    def meta_statuses() -> dict:
        return {
            "login_states": [
                types.LOGIN_STATE_LOGGED_IN,
                types.LOGIN_STATE_NOT_LOGGED_IN,
                types.LOGIN_STATE_UNKNOWN,
            ],
            "match_statuses": [
                types.MATCH_STATUS_MATCHED,
                types.MATCH_STATUS_MULTIPLE,
                types.MATCH_STATUS_UNMATCHED,
            ],
            "task_statuses": list(types.TASK_STATUSES),
            "task_transitions": {
                status: sorted(targets)
                for status, targets in task_manager.ALLOWED_TRANSITIONS.items()
            },
            "manual_confirm_required": sorted(task_manager.MANUAL_CONFIRM_REQUIRED),
            "search_outcomes": list(types.SEARCH_OUTCOMES),
        }

    return app


app = create_app()
