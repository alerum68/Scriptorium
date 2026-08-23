"""Pulled-forward Phase 2 test: PyInstaller hiddenimports coverage.

Antiquarian.py spawns every tool by re-executing itself with ``--module <dotted.path>``
plus :mod:`runpy` (the ARCH-1 revert). Under a frozen PyInstaller build that only works
when each target module was compiled into the bundle, i.e. listed in build.py's
``--hidden-import`` arguments. A missing entry does not fail the build - it fails at
click-time on an end user's machine - so the SCRIPT_PATHS <-> hidden-import parity is
asserted here, cheaply, on every CI run.

This test deliberately parses both files instead of importing Antiquarian.py:
importing it drags in tkinter/customtkinter and executes the settings-schema loaders,
none of which belong in a unit test.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANTIQUARIAN_PY = REPO_ROOT / "Antiquarian.py"
BUILD_PY = REPO_ROOT / "build.py"


def _module_level_literal(filename: Path, var_name: str):
    """Returns the value of a module-level ``VAR = <literal>`` assignment, via ast."""
    tree = ast.parse(filename.read_text(encoding="utf-8"), filename=str(filename))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var_name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{filename.name} no longer defines a literal {var_name!r}")


def _script_modules():
    script_paths = _module_level_literal(ANTIQUARIAN_PY, "SCRIPT_PATHS")
    assert isinstance(script_paths, dict) and script_paths, "SCRIPT_PATHS is empty?"
    modules = {}
    for key, rel_path in script_paths.items():
        normalized = str(rel_path).replace("\\\\", "/")
        assert normalized.endswith(".py"), f"{key}: unexpected script path {rel_path!r}"
        modules[key] = normalized[: -len(".py")].replace("/", ".")
    return modules


def test_every_launchable_script_has_a_hidden_import():
    build_text = BUILD_PY.read_text(encoding="utf-8")
    missing = []
    for key, module in sorted(_script_modules().items()):
        needle = f'"--hidden-import", "{module}"'
        if needle not in build_text:
            missing.append(f"{key} -> {module}")
    assert not missing, (
        "build.py's PyInstaller invocation is missing --hidden-import entries for: "
        + "; ".join(missing)
        + ". Frozen builds crash at click-time without them (see ARCH-1)."
    )


def test_build_declares_at_least_one_hidden_import_per_script():
    build_text = BUILD_PY.read_text(encoding="utf-8")
    declared = build_text.count('"--hidden-import"')
    assert declared >= len(_script_modules()), (
        f"build.py declares {declared} --hidden-import entries but Antiquarian.py "
        f"can launch {len(_script_modules())} scripts"
    )
