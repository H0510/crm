import sys
from pathlib import Path

# 让 tests 目录能直接 import wechat_service
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
