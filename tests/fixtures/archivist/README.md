# Archivist golden-file fixtures

Drop JSON payloads here that Archivist's builders consume directly; the filename's
suffix selects the entry point:

- `<name>.census.json`  -> `Census.run_census_flavor(data)`
- `<name>.scrip.json`   -> `General.run_general_flavor(data, Scrip.ScripProfile())`
- `<name>.general.json` -> `General.run_general_flavor(data, General.GeneralProfile())`

After adding a fixture, generate its snapshot once:

    ANT_UPDATE_GOLDEN=1 python -m pytest tests/test_gedcom_golden.py

Review the generated `tests/golden/archivist/<name>.ged` with `git diff` and commit it
alongside the fixture. From then on the test fails on any output drift, after masking
the lines that legitimately change between runs (run date/time, copyright year,
absolute media paths).
