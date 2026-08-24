"""
Standalone script backing Antiquarian's "Test Agy Connection" button.

Runs agy_client.check_or_prompt_auth once, deliberately interactively (may open a
browser for first-time Google sign-in) - this is meant to be run by a human, launched
from the GUI, not as part of an unattended batch. Exits 0 on success, 1 on failure, so
Antiquarian's own subprocess launcher reports the outcome the same way every other tool
script does.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent
load_dotenv(_THIS_DIR.parent / ".env", override=True)

sys.path.insert(0, str(_THIS_DIR))
import agy_client  # noqa: E402

DEFAULT_MODEL = "gemini-3.1-pro-high"


def main() -> None:
    model = os.getenv("AGY_MODEL_NAME") or DEFAULT_MODEL
    cli_bin = os.getenv("AGY_CLI_BIN") or agy_client.DEFAULT_CLI_BIN

    print(f"Testing agy connection (model={model}, cli_bin={cli_bin})...")
    print("If this is the first time, a browser window may open for Google sign-in.\n", flush=True)

    try:
        ok = agy_client.check_or_prompt_auth(model, cli_bin=cli_bin)
    except agy_client.AgyCallError as e:
        print(f"\n[FAILED] {e}")
        sys.exit(1)

    if ok:
        print("\n[SUCCESS] agy is authenticated and reachable.")
        sys.exit(0)
    else:
        print("\n[FAILED] agy call did not succeed. Check the output above for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
