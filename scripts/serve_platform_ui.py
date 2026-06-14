"""Start the local satmodel platform UI from the repository workspace."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


# Load the workspace source tree explicitly so the local UI always serves current edits.
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = WORKSPACE_ROOT / "src"
WEBAPP_PATH = SRC_ROOT / "satmodel" / "platform" / "webapp.py"


def _load_workspace_webapp():
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    spec = importlib.util.spec_from_file_location("satmodel.platform.webapp", WEBAPP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load workspace webapp from {WEBAPP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["satmodel.platform.webapp"] = module
    spec.loader.exec_module(module)
    return module


serve_platform_ui = _load_workspace_webapp().serve_platform_ui


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the local satmodel platform UI.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    serve_platform_ui(args.root, host=args.host, port=args.port, open_browser=args.open)


if __name__ == "__main__":
    main()
