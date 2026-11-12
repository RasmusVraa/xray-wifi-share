from __future__ import annotations

import sys


def main() -> int:
    if sys.platform != "win32":
        print("Только Windows")
        return 1

    from app.admin import is_admin, relaunch_as_admin

    # Always run elevated: SoftAP / ICS / tun2socks need admin.
    if not is_admin():
        relaunch_as_admin()
        return 0

    if "--job" in sys.argv:
        from app.elevated_job import run_job

        return run_job()

    from app.gui import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
