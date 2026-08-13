"""Entrypoint packaged as the C2B Desktop local analysis sidecar.

The Tauri shell owns all environment variables (SQLite path, local data root
and desktop-only mode).  Keeping this module tiny ensures the packaged
runtime executes exactly the same FastAPI application as Web and CLI.
"""
import os
import threading
import time

import uvicorn

from c2b.api.main import app


def _stop_when_desktop_exits() -> None:
    """Avoid leaving the one-file PyInstaller child alive after macOS quits.

    A PyInstaller one-file executable creates an extra bootstrap process, so
    killing only the Tauri process can otherwise orphan the actual Python
    server.  The shell passes its PID explicitly; this watcher is a local
    lifecycle guard and is deliberately inactive outside Desktop.
    """
    raw_pid = os.environ.get("C2B_DESKTOP_PARENT_PID", "")
    try:
        parent_pid = int(raw_pid)
    except ValueError:
        return
    if parent_pid <= 1:
        return

    while True:
        time.sleep(1)
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            os._exit(0)
        except PermissionError:
            # A permission error means the process is still owned by a live
            # user/session.  Never stop useful local analysis on that basis.
            continue


def main() -> None:
    threading.Thread(target=_stop_when_desktop_exits, daemon=True).start()
    uvicorn.run(
        app,
        host=os.environ.get("C2B_DESKTOP_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("C2B_DESKTOP_API_PORT", "8123")),
        log_level=os.environ.get("C2B_DESKTOP_LOG_LEVEL", "warning"),
    )


if __name__ == "__main__":
    main()
