"""Loopback-only launcher for the SYNVIO Core local API."""

from __future__ import annotations

import os
import sys


def main() -> None:
    host = os.environ.get("SYNVIO_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("SYNVIO web API may only bind to localhost.")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - install-extra diagnostic
        raise SystemExit("Install the web extra: python -m pip install -e '.[web,dev]'") from exc
    uvicorn.run(
        "synvio.web:app", host="127.0.0.1", port=int(os.environ.get("SYNVIO_PORT", "8000")),
        log_level=os.environ.get("SYNVIO_LOG_LEVEL", "info"),
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())