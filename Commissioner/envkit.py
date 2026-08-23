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


def load_tool_env(tool_dir) -> None:
    """Loads <repo root>/.env then <tool_dir>/.env, both override=True, so a tool's own
    .env always beats the same key in the global one, and either beats anything the
    parent process already put in the environment."""
    tool_dir = Path(tool_dir).resolve()
    load_dotenv(tool_dir.parent / ".env", override=True)
    load_dotenv(tool_dir / ".env", override=True)
