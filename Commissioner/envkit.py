"""Shared .env loading for every tool in the toolbox (BUG-8).

Each tool used to carry its own pair of load_dotenv() lines, and they disagreed on the
override flag - some True, some False - so which file won depended on which tool ran
and on whatever was already in the process environment. Standardized here once: the
repo root's global .env loads first, then the tool's own subfolder .env loads second,
both with override=True. Net precedence: tool .env > global .env > inherited
environment variables.
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
