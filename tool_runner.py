"""Explicit subprocess launcher for Antiquarian's child tools (BUG-4 / ARCH-1).

Antiquarian.py's _run_subprocess spawns this file by path instead of re-executing the
GUI entry point with a --module flag: the old self-relaunch dragged tkinter/
customtkinter and the whole app module graph into every child process, and keyed off
__file__, which lands inside PyInstaller's bundle when frozen. This file does exactly
one thing - run the named module as __main__ - so it stays trivially launchable and
bundleable (see build.py's --hidden-import tool_runner).
"""
import runpy
import sys

runpy.run_module(sys.argv[1], run_name="__main__")
