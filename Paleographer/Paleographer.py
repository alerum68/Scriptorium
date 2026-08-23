"""
Paleographer: thin dispatcher for AI-driven document extraction and Scrip-specific
enrichment.

Extraction (record-type-generic, driven entirely by the active .pmt file) lives in
Extract.py. Scrip-only enrichment (enrich, crosscheck, partition, resolve-names)
lives in ScripTools.py. Antiquarian.py launches this as a subprocess with
cwd=Paleographer/, so Extract.py and ScripTools.py import as plain sibling modules.
"""

import sys
from pathlib import Path

# When executed via Antiquarian.py's runpy router, cwd is the project root, so
# Paleographer/ isn't in sys.path by default. Add it so sibling imports (Extract,
# ScripTools) work - mirrors Voyageur.py's own fix for the same router quirk.
_MODULE_DIR = str(Path(__file__).resolve().parent)
if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)

ENRICHMENT_MODES = ("enrich", "crosscheck", "partition", "resolve-names")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ENRICHMENT_MODES:
        # Unlike Voyageur.py's mode token, sys.argv[1] is deliberately left in place here:
        # ScripTools.main() parses it itself as its own argparse "mode" positional.
        import ScripTools
        ScripTools.main()
    else:
        import Extract
        Extract.main()


if __name__ == "__main__":
    main()
