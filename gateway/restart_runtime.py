"""Gateway restart and resume runtime helpers."""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from gateway.platforms.base import MessageEvent, MessageType
from utils import atomic_json_write


def increment_restart_failure_counts(
    *,
    hermes_home: Path,
    failure_file: str,
    active_session_keys: set,
    atomic_json_write_fn: Callable[..., Any] = atomic_json_write,
) -> None:
    path = hermes_home / failure_file
    try:
        import json

        counts = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        counts = {}

    new_counts = {key: counts.get(key, 0) + 1 for key in active_session_keys}
    try:
        atomic_json_write_fn(path, new_counts, indent=None)
    except Exception:
        pass


def suspend_stuck_loop_sessions(
    *,
    hermes_home: Path,
    failure_file: str,
    threshold: int,
    session_store: Any,
    logger: Any,
) -> int:
    path = hermes_home / failure_file
    if not path.exists():
        return 0

    try:
        import json

        counts = json.loads(path.read_text())
    except Exception:
        return 0

    suspended = 0
    for session_key in [key for key, value in counts.items() if value >= threshold]:
        try:
            entry = session_store._entries.get(session_key)
            if entry and not entry.suspended:
                entry.suspended = True
                suspended += 1
                logger.warning(
                    "Auto-suspended stuck session %s (active across %d "
                    "consecutive restarts — likely a stuck loop)",
                    session_key,
                    counts[session_key],
                )
        except Exception:
            pass

    if suspended:
        try:
            session_store._save()
        except Exception:
            pass

    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass

    return suspended


def clear_restart_failure_count(
    *,
    hermes_home: Path,
    failure_file: str,
    session_key: str,
    atomic_json_write_fn: Callable[..., Any] = atomic_json_write,
) -> None:
    path = hermes_home / failure_file
    if not path.exists():
        return
    try:
        import json

        counts = json.loads(path.read_text())
        if session_key in counts:
            del counts[session_key]
            if counts:
                atomic_json_write_fn(path, counts, indent=None)
            else:
                path.unlink(missing_ok=True)
    except Exception:
        pass


async def launch_detached_restart_command(
    *,
    resolve_hermes_bin: Callable[[], list[str] | None],
    logger: Any,
) -> None:
    hermes_cmd = resolve_hermes_bin()
    if not hermes_cmd:
        logger.error("Could not locate hermes binary for detached /restart")
        return

    current_pid = os.getpid()
    if sys.platform == "win32":
        from hermes_cli._subprocess_compat import windows_detach_popen_kwargs

        cmd_argv = [*hermes_cmd, "gateway", "restart"]
        watcher = textwrap.dedent(
            """
            import os, subprocess, sys, time
            pid = int(sys.argv[1])
            cmd = sys.argv[2:]
            deadline = time.monotonic() + 120

            def _alive(p):
                if os.name == 'nt':
                    import ctypes
                    k32 = ctypes.windll.kernel32
                    k32.OpenProcess.restype = ctypes.c_void_p
                    k32.WaitForSingleObject.restype = ctypes.c_uint
                    k32.GetLastError.restype = ctypes.c_uint
                    h = k32.OpenProcess(0x1000 | 0x100000, False, int(p))
                    if not h:
                        return k32.GetLastError() != 87
                    try:
                        return k32.WaitForSingleObject(h, 0) == 0x102
                    finally:
                        k32.CloseHandle(h)
                try:
                    os.kill(int(p), 0)
                    return True
                except ProcessLookupError:
                    return False
                except PermissionError:
                    return True
                except OSError:
                    return False

            while time.monotonic() < deadline:
                if not _alive(pid):
                    break
                time.sleep(0.2)
            _CREATE_NEW_PROCESS_GROUP = 0x00000200
            _DETACHED_PROCESS = 0x00000008
            _CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS | _CREATE_NO_WINDOW,
            )
            """
        ).strip()
        subprocess.Popen(
            [sys.executable, "-c", watcher, str(current_pid), *cmd_argv],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **windows_detach_popen_kwargs(),
        )
        return

    cmd = " ".join(shlex.quote(part) for part in hermes_cmd)
    shell_cmd = (
        f"while kill -0 {current_pid} 2>/dev/null; do sleep 0.2; done; "
        f"{cmd} gateway restart"
    )
    setsid_bin = shutil.which("setsid")
    argv = [setsid_bin, "bash", "-lc", shell_cmd] if setsid_bin else ["bash", "-lc", shell_cmd]
    subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def schedule_resume_pending_sessions(
    *,
    session_store: Any,
    adapters: dict,
    background_tasks: set,
    auto_resume_reasons: frozenset[str],
    freshness_window: float,
    logger: Any,
) -> int:
    try:
        with session_store._lock:
            session_store._ensure_loaded_locked()
            candidates = [
                entry for entry in session_store._entries.values()
                if entry.resume_pending
                and not entry.suspended
                and entry.origin is not None
                and entry.resume_reason in auto_resume_reasons
            ]
    except Exception as exc:
        logger.warning("Failed to enumerate resume-pending sessions: %s", exc)
        return 0

    now = datetime.now()
    scheduled = 0
    for entry in candidates:
        marker = entry.last_resume_marked_at or entry.updated_at
        if marker is not None and (now - marker).total_seconds() > freshness_window:
            continue

        source = entry.origin
        adapter = adapters.get(source.platform)
        if adapter is None:
            logger.debug(
                "Skipping auto-resume for %s: adapter not ready for %s",
                entry.session_key,
                getattr(source.platform, "value", source.platform),
            )
            continue

        event = MessageEvent(
            text="",
            message_type=MessageType.TEXT,
            source=source,
            internal=True,
        )
        task = asyncio.create_task(adapter.handle_message(event))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        scheduled += 1

    if scheduled:
        logger.info(
            "Scheduled auto-resume for %d restart-interrupted session(s)",
            scheduled,
        )
    return scheduled
