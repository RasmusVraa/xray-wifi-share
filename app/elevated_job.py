from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from .engine import Engine, StartRequest
from .paths import JOB_FILE, LOG_FILE, STATUS_FILE, WORK, ensure_dirs
from .procutil import (
    clear_supervisor_pid,
    kill_our_share_processes,
    kill_supervisor,
    write_supervisor_pid,
)

STOP_FLAG = WORK / "stop.flag"
KILL_FLAG = WORK / "kill.flag"


def _write_status(state: str, message: str = "") -> None:
    ensure_dirs()
    STATUS_FILE.write_text(
        json.dumps(
            {
                "state": state,
                "message": message,
                "ts": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _append_log(line: str) -> None:
    ensure_dirs()
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _clear_flags() -> None:
    for p in (STOP_FLAG, KILL_FLAG):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


def _hard_cleanup(engine: Engine, reason: str) -> None:
    _append_log(f"cleanup: {reason}")
    try:
        engine.force_kill()
    except Exception as exc:
        _append_log(f"force_kill err: {exc}")
        kill_our_share_processes()
        kill_supervisor()


def run_job() -> int:
    ensure_dirs()
    if not JOB_FILE.exists():
        _write_status("error", "job.json не найден")
        return 1

    try:
        job = json.loads(JOB_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        _write_status("error", f"job.json: {exc}")
        return 1

    action = str(job.get("action") or "").lower()
    engine = Engine(log=_append_log)

    try:
        if action == "start":
            # Always wipe previous share stack first
            _write_status("starting", "Очистка прошлого запуска...")
            kill_supervisor()
            _hard_cleanup(engine, "before-start")
            _clear_flags()
            write_supervisor_pid()

            _write_status("starting", "Запуск...")
            outbound = job.get("outbound")
            if not isinstance(outbound, dict):
                outbound = None
            engine.start(
                StartRequest(
                    link=str(job.get("link") or ""),
                    wifi_adapter=str(job.get("wifi_adapter") or ""),
                    ssid=str(job.get("ssid") or "Xray_WiFi_Share"),
                    password=str(job.get("password") or ""),
                    outbound=outbound,
                    remark=str(job.get("remark") or ""),
                    kind=str(job.get("kind") or ""),
                    server_host=str(job.get("server_host") or ""),
                    server_port=int(job.get("server_port") or 0),
                )
            )
            _write_status("running", "Раздача активна")
            while engine.running:
                if KILL_FLAG.exists():
                    _append_log("Получен kill.flag")
                    _hard_cleanup(engine, "kill.flag")
                    clear_supervisor_pid()
                    _write_status("stopped", "Убито")
                    return 0
                if STOP_FLAG.exists():
                    _append_log("Получен stop.flag")
                    break
                time.sleep(0.35)
            engine.stop()
            kill_our_share_processes()
            clear_supervisor_pid()
            _write_status("stopped", "Остановлено")
            return 0

        if action == "stop":
            STOP_FLAG.write_text("1", encoding="utf-8")
            _write_status("stopping", "Остановка...")
            # Give supervisor a moment to exit gracefully
            time.sleep(1.0)
            kill_supervisor()
            _hard_cleanup(engine, "stop-job")
            clear_supervisor_pid()
            _write_status("stopped", "Остановлено")
            return 0

        if action == "kill":
            KILL_FLAG.write_text("1", encoding="utf-8")
            _write_status("killing", "Принудительное завершение...")
            time.sleep(0.4)
            kill_supervisor()
            _hard_cleanup(engine, "kill-job")
            clear_supervisor_pid()
            _write_status("stopped", "Убито")
            return 0

        _write_status("error", f"Неизвестное действие: {action}")
        return 1
    except Exception as exc:
        _append_log(f"Ошибка: {exc}")
        try:
            _hard_cleanup(engine, "exception")
        except Exception:
            pass
        clear_supervisor_pid()
        _write_status("error", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(run_job())
