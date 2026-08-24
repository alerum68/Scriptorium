# Developer Workflow & Guidelines

This document outlines environment setup, testing standards, linter rules, and commit practices for Antiquarian contributors.

---

## Environment Setup

1. **Python Version**: Python 3.12 or newer.
2. **Virtual Environment**:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   ```
3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   `requirements.txt` is a fully-pinned lockfile (direct and transitive versions) generated
   from `requirements.in` via `pip-tools` - see the header comment in `requirements.txt` for
   the regenerate command. Add `-r requirements-optional.txt` if you need Voyageur's
   browser-automation code paths (Playwright-driven Keystone queries, CDP cookie reading) -
   neither is required for the rest of the app.

---

## Testing Standards

All code changes must maintain 100% test suite pass rates.

### Running Tests
Run the complete test suite with `pytest`:
```bash
python -m pytest
```

Run specific module test directories:
```bash
python -m pytest Paleographer/tests Commissioner/tests Voyageur/tests -v
```

### Test Organization
Tests live in dedicated `tests/` directories within each module:
- `Archivist/tests/`
- `Commissioner/tests/`
- `Gazetteer/tests/`
- `PDFix/tests/`
- `Paleographer/tests/`
- `Registrar/tests/`
- `AgyCli/tests/`
- `Voyageur/tests/`

### Guidelines
- **Network Isolation**: Unit tests must never make real HTTP requests or open live browser sessions. Mock network client calls (`requests`, `urllib`, `lac_client`).
- **Fixture Reusability**: Place shared test fixtures in `conftest.py` within the module test directory.

---

## Code Quality & Style Rules

### Linter Policy
All Python code must pass `pycodestyle` with a max line length of 120:
```bash
python -m pycodestyle --max-line-length=120
```

Zero linter violations are allowed on any committed branch.

### Compilation Check
Verify syntax across edited files before committing:
```bash
python -m py_compile <path/to/file.py>
```

---

## Commit & Attribution Rules
- **Commit Granularity**: Commit frequently in logical, self-contained units.
- **Branch Strategy**: Feature work and refactoring occur on `Unify` or dedicated topic branches.
