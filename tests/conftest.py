"""Minimal Deluge stubs for running unit tests without Deluge installed.

The normal unit-test run always prefers the stubs, so the developer's real
Deluge installation cannot change the result. Set ``DISKGUARD_REAL_DELUGE=1``
in the compatibility job to import the real Deluge packages instead.
"""

import os
import sys
from pathlib import Path

if os.environ.get("DISKGUARD_REAL_DELUGE") != "1":
    sys.path.insert(0, str(Path(__file__).parent / "stubs"))
