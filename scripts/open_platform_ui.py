"""Launch the local satmodel platform UI in a detached process."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SERVE_SCRIPT = WORKSPACE_ROOT / "scripts" / "serve_platform_ui.py"
STDOUT_LOG = WORKSPACE_ROOT / "platform_ui.stdout.log"
STDERR_LOG = WORKSPACE_ROOT / "platform_ui.stderr.log"


def _ui_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=2) as response:
            if response.status < 200 or response.status >= 500:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("status") == "ok"
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _choose_port(host: str, preferred_port: int, max_port: int) -> tuple[int, bool]:
    for port in range(preferred_port, max_port + 1):
        url = f"http://{host}:{port}"
        if _ui_health(url):
            return port, True
        if _port_available(port, host):
            return port, False
    raise RuntimeError(f"no available UI port found between {preferred_port} and {max_port}")


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    return flags


def _start_server(host: str, port: int) -> subprocess.Popen[bytes]:
    if STDOUT_LOG.exists():
        STDOUT_LOG.unlink()
    if STDERR_LOG.exists():
        STDERR_LOG.unlink()
    stdout_handle = STDOUT_LOG.open("wb")
    stderr_handle = STDERR_LOG.open("wb")
    command = [sys.executable, str(SERVE_SCRIPT), "--root", ".", "--host", host, "--port", str(port)]
    try:
        flags = _creationflags()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(WORKSPACE_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=flags,
                close_fds=False if os.name == "nt" else True,
            )
        except PermissionError:
            fallback_flags = flags & ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            process = subprocess.Popen(
                command,
                cwd=str(WORKSPACE_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=fallback_flags,
                close_fds=False if os.name == "nt" else True,
            )
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return process


def _wait_for_server(url: str, timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _ui_health(url):
            return True
        time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the local satmodel platform UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--preferred-port", type=int, default=8765)
    parser.add_argument("--max-port", type=int, default=8775)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    port, existing = _choose_port(args.host, args.preferred_port, args.max_port)
    url = f"http://{args.host}:{port}"

    if not existing:
        process = _start_server(args.host, port)
        if not _wait_for_server(url):
            if process.poll() is None:
                process.terminate()
            raise RuntimeError("platform UI did not start successfully; check platform_ui.stderr.log")

    if not args.no_browser:
        webbrowser.open(url)

    print(
        f"satmodel platform UI {'already running' if existing else 'started'}: {url}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
