"""
Makes agy_client.py importable as a plain top-level module (matching how
agy_engine.py itself imports it) when pytest is run from anywhere.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
