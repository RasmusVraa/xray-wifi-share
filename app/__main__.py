from .gui import run_app
from .admin import is_admin, relaunch_as_admin
import sys


def main() -> int:
    if not is_admin():
        relaunch_as_admin()
        return 0
    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
