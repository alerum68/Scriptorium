"""
Automated code quality and lint suite matching PyCharm inspections.
Runs flake8 (pycodestyle + pyflakes) programmatically across all modules.
"""

from pathlib import Path
import subprocess
import sys


def test_code_quality_flake8():
    """Verify that flake8 (pycodestyle + pyflakes) reports 0 errors across the codebase."""
    repo_root = Path(__file__).resolve().parent.parent
    cmd = [
        sys.executable,
        "-m",
        "flake8",
        "--max-line-length=120",
        "--exclude=.venv,venv,.git,__pycache__,.pytest_cache,.opencode,build,dist",
        "Archivist",
        "Commissioner",
        "Paleographer",
        "Voyageur",
        "PDFix",
        "Registrar",
        "Gazetteer",
        "AgyCli",
        "Antiquarian.py",
        "tests",
    ]
    result = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    assert result.returncode == 0, f"Lint violations found:\n{result.stdout}\n{result.stderr}"
