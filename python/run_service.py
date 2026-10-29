"""后端服务入口：给 PyInstaller 打包用，行为和 `uvicorn wechat_service.main:app` 一致。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


def _prepare_data_dir() -> None:
    """打包后数据库放在 exe 旁边的 data 目录，避免写进 PyInstaller 的临时解压目录。"""
    if os.environ.get("WECHAT_DATA_DIR"):
        return
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    os.environ["WECHAT_DATA_DIR"] = str(base / "data")


_prepare_data_dir()

from wechat_service.main import app  # noqa: E402  必须在设置数据目录之后导入


def main() -> None:
    host = os.environ.get("WECHAT_HOST", "127.0.0.1")
    port = int(os.environ.get("WECHAT_PORT", "8756"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
