"""UG-DCMS installer runtime verification.

Use a script file instead of ``python -c`` because Windows PowerShell 5.1 /
Start-Process can split a multi-statement -c argument incorrectly.
"""
from __future__ import annotations

import pathlib
import sys


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-runtime.py BACKEND_DIR SUCCESS_MARKER")
    backend = pathlib.Path(sys.argv[1]).resolve(strict=True)
    marker = pathlib.Path(sys.argv[2])
    sys.path.insert(0, str(backend))
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401
    import psycopg  # noqa: F401
    import bcrypt  # noqa: F401
    import pydantic  # noqa: F401
    import openpyxl  # noqa: F401
    import app.main  # noqa: F401
    marker.write_text("UG-DCMS runtime import check OK\n", encoding="utf-8")
    print("UG-DCMS runtime import check OK")


if __name__ == "__main__":
    main()
