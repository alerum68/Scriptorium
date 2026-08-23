"""
Voyageur - thin dispatcher for the GUI's A/FS/LAC gather buttons.

Antiquarian.py launches this as a subprocess with cwd=Voyageur/ and the source code
(A/FS/LAC) as sys.argv[1], so A/FS/LAC import as plain sibling modules and each
provider's own main() sees exactly the CLI arguments Antiquarian.py meant for it.
"""

import sys
from pathlib import Path

# When executed via Antiquarian.py's runpy router, cwd is the project root, so
# Voyageur/ isn't in sys.path by default. Add it so sibling imports (A, FS) work.
_MODULE_DIR = str(Path(__file__).resolve().parent)
if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)
SOURCES = ("A", "FS", "LAC", "HBCA")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in SOURCES:
        print(f"[ERROR] Usage: python Voyageur.py <source>, where <source> is one of: "
              f"{', '.join(SOURCES)}.")
        sys.exit(1)
    source = sys.argv[1]
    del sys.argv[1]  # strip the mode token so A/FS/LAC/HBCA's own argv[1:] parsing never sees it
    if source == "A":
        import A
        A.main()
    elif source == "FS":
        import FS
        FS.main()
    elif source == "LAC":
        import LAC
        LAC.main()
    elif source == "HBCA":
        import HBCA
        HBCA.main()


if __name__ == "__main__":
    main()
