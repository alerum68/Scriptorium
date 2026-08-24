"""Shared .env loading for every tool in the toolbox (BUG-8).

Each tool used to carry its own pair of load_dotenv() lines, and they disagreed on the
override flag - some True, some False - so which file won depended on which tool ran
and on whatever was already in the process environment. Standardized here once: the
tool's own subfolder .env loads first, then the repo root's global .env loads second,
both at every caller's actual default of override=False (no caller in this codebase
passes True). load_dotenv(override=False) only fills in a key that isn't already set,
so net precedence is: already-set environment variables (e.g. a CI runner's exports,
or ARCH-4's curated per-tool env passed to a launched subprocess) > tool .env > global
.env. Pass override=True explicitly if a given caller ever needs .env files to win
over an inherited variable instead.
"""
from pathlib import Path

from dotenv import load_dotenv


def load_tool_env(tool_dir, override: bool = False) -> None:
    """Loads <tool_dir>/.env then <repo root>/.env, so a tool's own .env always takes
    precedence over the global one, while allowing explicit environment variables (e.g. from
    test runners) to remain authoritative when override=False."""
    tool_dir = Path(tool_dir).resolve()
    load_dotenv(tool_dir / ".env", override=override)
    load_dotenv(tool_dir.parent / ".env", override=override)
