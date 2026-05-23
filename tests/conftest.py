from __future__ import annotations

import sys
from pathlib import Path


# 测试直接从仓库源码目录导入 satmodel，避免必须先执行 pip install -e .
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
