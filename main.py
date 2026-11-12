from __future__ import annotations

import sys


def main() -> int:
    if sys.platform != "win32":
        print("Только Windows")
        return 1

    if "--job" in sys.argv:
        from app.elevated_job import run_job

        return run_job()

    # GUI runs without forced UAC so clipboard paste works.
    # Privileged start/stop/kill are launched via UAC separately.
    from app.gui import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
