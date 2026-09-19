"""Import paths for the bridge: sim/ (nav.py) and fw/tools/ (proto.py)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / 'sim', REPO / 'fw' / 'tools'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
