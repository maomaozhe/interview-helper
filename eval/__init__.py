"""Frozen-gold evaluation utilities."""

import sys
from pathlib import Path

# All repository entry points, including historical ``python -m eval.*``
# commands, must use the same src checkout as pytest without requiring an
# editable install. A previously imported foreign package is caught by preflight.
_source = str(Path(__file__).resolve().parents[1] / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
