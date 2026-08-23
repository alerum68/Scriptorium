"""TEST-3: Router smoke test.

Exercises the two routing layers without launching anything:

1. Antiquarian.py's SCRIPT_PATHS - every entry must point at a file that exists, and
   the self-respawn bootstrap that carries them (``--module`` + ``runpy.run_module``)
   must still be present. That bootstrap IS the ARCH-1 revert; if someone reintroduces
   spawning-by-path (or drops runpy), frozen builds break in ways no build step catches.
2. Voyageur's source dispatch - every repository advertised in Antiquarian.py's
   VOYAGEUR_SOURCES must have a Voyageur/<code>.py implementing ``main()``, and the
   thin Voyageur.py dispatcher must reference it.

Like the hiddenimports test, everything is AST/source inspection: importing
Antiquarian.py or Voyageur.py would drag GUI/browser dependencies into the test run.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANTIQUARIAN_PY = REPO_ROOT / "Antiquarian.py"
VOYAGEUR_DIR = REPO_ROOT / "Voyageur"


def _antiquarian_source():
    return ANTIQUARIAN_PY.read_text(encoding="utf-8")


def _antiquarian_tree():
    return ast.parse(_antiquarian_source(), filename=str(ANTIQUARIAN_PY))


def _module_level_literal(tree, var_name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var_name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"Antiquarian.py no longer defines a literal {var_name!r}")


def test_every_script_path_exists_on_disk():
    script_paths = _module_level_literal(_antiquarian_tree(), "SCRIPT_PATHS")
    missing = sorted(
        rel for rel in script_paths.values()
        if not (REPO_ROOT / rel).is_file()
    )
    assert not missing, f"SCRIPT_PATHS points at files that do not exist: {missing}"


def test_self_respawn_bootstrap_is_intact():
    """Guards the ARCH-1 revert: tools must be spawned via --module + runpy.run_module,
    never by script path (which cannot work inside a frozen PyInstaller bundle)."""
    src = _antiquarian_source()
    assert '"--module"' in src, "the --module argparse flag vanished from Antiquarian.py"
    assert "runpy.run_module" in src, (
        "ARCH-1 regression: Antiquarian.py must dispatch subprocesses through "
        "runpy.run_module; spawning by path cannot work when frozen"
    )


def test_voyageur_sources_have_subscripts_with_main():
    sources = _module_level_literal(_antiquarian_tree(), "VOYAGEUR_SOURCES")
    assert sources, "VOYAGEUR_SOURCES is empty?"
    problems = []
    for entry in sources:
        code, label = entry[0], entry[1]
        sub = VOYAGEUR_DIR / f"{code}.py"
        if not sub.is_file():
            problems.append(f"{code} ({label}): {sub.name} is missing")
            continue
        sub_tree = ast.parse(sub.read_text(encoding="utf-8"), filename=str(sub))
        if not any(isinstance(n, ast.FunctionDef) and n.name == "main"
                   for n in sub_tree.body):
            problems.append(f"{code} ({label}): {sub.name} defines no module-level main()")
    assert not problems, "; ".join(problems)


def test_voyageur_dispatcher_references_every_source():
    sources = _module_level_literal(_antiquarian_tree(), "VOYAGEUR_SOURCES")
    dispatcher = VOYAGEUR_DIR / "Voyageur.py"
    assert dispatcher.is_file(), "Voyageur/Voyageur.py (the dispatcher) is missing"
    disp_src = dispatcher.read_text(encoding="utf-8")
    unreferenced = []
    for entry in sources:
        code = entry[0]
        referenced = (
            f'"{code}"' in disp_src
            or f"'{code}'" in disp_src
            or "{code}" in disp_src          # dynamic f-string dispatch
            or f"{code}.py" in disp_src
        )
        if not referenced:
            unreferenced.append(code)
    assert not unreferenced, (
        f"Voyageur.py's dispatcher never references source code(s): {unreferenced}"
    )
