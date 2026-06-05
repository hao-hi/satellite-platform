"""Start the local satmodel platform UI from the repository workspace."""

from __future__ import annotations

import argparse

from satmodel.platform.webapp import serve_platform_ui


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
