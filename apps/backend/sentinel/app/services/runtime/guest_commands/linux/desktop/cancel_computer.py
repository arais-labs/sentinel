import json
import os
import signal
import sys
from pathlib import Path
from uuid import UUID

request_id = str(UUID(sys.argv[1]))
root = Path("/run/sentinel-desktop")
if root.exists():
    (root / ("cancel-" + request_id)).touch()
    try:
        active = json.loads((root / "computer-active.json").read_text())
        if active.get("request_id") == request_id:
            os.kill(active["pid"], signal.SIGTERM)
    except (OSError, ValueError, KeyError):
        pass
