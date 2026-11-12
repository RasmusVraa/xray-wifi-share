import sys

from .admin import is_admin, relaunch_as_admin


def main() -> int:
    if not is_admin():
        relaunch_as_admin()
        return 0
    if "--job" in sys.argv:
        from .elevated_job import run_job

        return run_job()
    from .gui import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
