"""Build an installable Chrome/Edge ZIP for one Kaelia HTTPS origin."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

SOURCE = Path(__file__).resolve().parents[1] / "extensions" / "browser"
FILES = ("README.md", "popup.css", "popup.html", "popup.js",
         "tab-controls.js", "worker.js", "workspace-bridge.js")


def workspace_origin(value: str) -> str:
    url = urlsplit(value.strip().rstrip("/"))
    hostname = url.hostname or ""
    dns = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}"
    if url.scheme != "https" or url.netloc.lower() != hostname or not re.fullmatch(dns, hostname):
        raise ValueError("Use an exact HTTPS DNS origin, such as https://workspace.example.com.")
    if url.path or url.query or url.fragment:
        raise ValueError("The workspace origin must not contain a path, query or fragment.")
    return f"https://{hostname}"


def package(origin: str, destination: Path) -> Path:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    original = manifest["host_permissions"][0].removesuffix("/*")
    pattern = workspace_origin(origin) + "/*"
    manifest["host_permissions"] = [pattern]
    manifest["content_scripts"][0]["matches"] = [pattern]
    destination = destination.resolve()
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("browser/manifest.json", json.dumps(manifest, indent=2) + "\n")
        for name in FILES:
            if name == "README.md":
                guide = (SOURCE / name).read_text(encoding="utf-8").replace(original, origin)
                archive.writestr("browser/" + name, guide)
            else:
                archive.write(SOURCE / name, "browser/" + name)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(package(workspace_origin(args.origin), args.out))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
