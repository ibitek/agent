"""Run the full suite with no real keys and external network access blocked."""

import ipaddress
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
for name in list(os.environ):
    if any(part in name for part in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):
        os.environ.pop(name, None)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
_connect = socket.socket.connect


def offline_connect(sock, address):
    if isinstance(address, tuple):
        try:
            local = address[0] == "localhost" or ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            local = False
        if local:
            return _connect(sock, address)
    raise RuntimeError("External network disabled during SYNVIO verification")


socket.socket.connect = offline_connect

if __name__ == "__main__":
    import pytest

    with tempfile.TemporaryDirectory(prefix="synvio-verify-") as temporary:
        os.environ["OPENJARVIS_HOME"] = str(Path(temporary) / "sdk")
        os.environ["TEMP"] = temporary
        os.environ["TMP"] = temporary
        raise SystemExit(pytest.main([*(sys.argv[1:] or [str(ROOT / "tests")]), "-q", "-p", "no:cacheprovider", "--basetemp", str(Path(temporary) / "pytest")]))
