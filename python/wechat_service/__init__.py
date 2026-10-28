"""GeoAI 微信桌面自动化模块的 Python 本地服务。"""

import sys
from pathlib import Path

# 仓库根目录加入模块搜索路径，便于引用 shared/ 下的跨端共享契约
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

__version__ = "0.1.0"
