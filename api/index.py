from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from synvio.cloud.app import app  # noqa: E402,F401
