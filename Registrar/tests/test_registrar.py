"""
Tests for Registrar's RootsMagic duplicate extraction and fuzzy matching logic -
covering database person/family extraction (extract_people_from_rm), fuzzy duplicate
detection across pass 1 and pass 2 (find_fuzzy_duplicates), and core matching helpers
(_evaluate_family_context, _build_match_record).
"""

import sqlite3
import pandas as pd
import pytest

import Registrar as reg


@pytest.fixture
def sample_rm_db(tmp_path):
    """
    Creates a temporary SQLite database with RootsMagic tables (NameTable, FamilyTable, ChildTable)
    and populates test individual and family relationship rows for database extraction testing.
    """
    db_path = tmp_path / "test_tree.rmtree"
    conn = sqlite3.connect(str(db_path))
    conn.create_collation("RMNOCASE", reg.rmnocase)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE NameTable (
            OwnerID INTEGER,
            Given TEXT,
            Surname TEXT,
            BirthYear INTEGER,
            IsPrimary INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE FamilyTable (
            FamilyID INTEGER PRIMARY KEY,
            FatherID INTEGER,
            MotherID INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE ChildTable (
            FamilyID INTEGER,
            ChildID INTEGER
        )
    """)

    # Populate primary names
    names = [
        (1, "Joseph", "Hougton", 1815, 1),
        (2, "James", "Depar", 1820, 1),
        (3, "Vide", "Depar", 1826, 1),
        (4, "Lucretia", "Depar", 1848, 1),
        (5, "John", "Doe", None, 1),  # Non-numeric / NULL birth year
        (6, "Jane", "Doe", 1815, 0),  # Non-primary name (should be ignored)
    ]
    cursor.executemany("INSERT INTO NameTable VALUES (?, ?, ?, ?, ?)", names)

    # Populate family relationships: James Depar (2) & Vide Depar (3) as parents of Lucretia (4)
    cursor.execute("INSERT INTO FamilyTable VALUES (100, 2, 3)")
    cursor.execute("INSERT INTO ChildTable VALUES (100, 4)")

    conn.commit()
    conn.close()
    return str(db_path)


# ==========================================
# _evaluate_family_context HELPER TESTS
# ==========================================

def test_evaluate_family_context_empty_lists_returns_false_false():
    """When either or both individuals have no recorded relatives, family matching cannot verify
    a link nor infer a conflict (returns family_match=False, family_conflict=False)."""
    match, conflict = reg._evaluate_family_context([], ["James Depar"])
    assert match is False
    assert conflict is False

    match, conflict = reg._evaluate_family_context([], [])
    assert match is False
    assert conflict is False


def test_evaluate_family_context_matching_relatives_returns_true_false():
    """If at least one relative's name matches above FAMILY_MATCH_THRESHOLD (default 75),
    family_match must evaluate to True and family_conflict must evaluate to False."""
    p1_rels = ["Vide Depar", "Joseph Hougton"]
    p2_rels = ["Vide Depar", "Unknown Relative"]
    match, conflict = reg._evaluate_family_context(p1_rels, p2_rels)
    assert match is True
    assert conflict is False


def test_evaluate_family_context_conflicting_relatives_returns_false_true():
    """If both individuals have relatives populated but none match, family_conflict must evaluate
    to True to prevent false-positive duplicate merging across distinct families."""
    p1_rels = ["Vide Depar"]
    p2_rels = ["Elizabeth Smith"]
    match, conflict = reg._evaluate_family_context(p1_rels, p2_rels)
    assert match is False
    assert conflict is True


# ==========================================
# _build_match_record HELPER TESTS
# ==========================================

def test_build_match_record_structures_dict_correctly():
    """Confirms _build_match_record formats output dictionary fields consistently across Pass 1
    and Pass 2 fuzzy matching passes."""
    p1 = {"Given": "Joseph", "Surname": "Hougton", "FullName": "Joseph Hougton"}
    p2 = {"PersonID": 10, "Given": "Joseph", "Surname": "Houghton", "FullName": "Joseph Houghton"}

    rec = reg._build_match_record(
        score=92, family_match=True, p1_id=1, p1=p1, p2=p2,
        birth_year_1=1815, birth_year_2=1816, age_gap=1
    )

    assert rec["Score"] == 92
    assert rec["Family_Verified"] is True
    assert rec["ID_1"] == 1
    assert rec["Given_1"] == "Joseph"
    assert rec["Surname_1"] == "Hougton"
    assert rec["Name_1"] == "Joseph Hougton"
    assert rec["BirthYear_1"] == 1815
    assert rec["ID_2"] == 10
    assert rec["Given_2"] == "Joseph"
    assert rec["Surname_2"] == "Houghton"
    assert rec["Name_2"] == "Joseph Houghton"
    assert rec["BirthYear_2"] == 1816
    assert rec["Age_Gap"] == 1


# ==========================================
# extract_people_from_rm TESTS
# ==========================================

def test_extract_people_from_rm_missing_file_raises_error():
    """Attempting to extract from a non-existent database file must raise FileNotFoundError immediately."""
    with pytest.raises(FileNotFoundError, match="Could not find RM database"):
        reg.extract_people_from_rm("non_existent_db.rmtree")


def test_extract_people_from_rm_parses_names_and_family_relationships(sample_rm_db):
    """Verifies extract_people_from_rm queries primary names, coerces missing/NULL birth years to 0,
    and builds accurate RelativesList mappings for spouses and children."""
    df = reg.extract_people_from_rm(sample_rm_db)

    # 5 primary people inserted (non-primary row 6 omitted)
    assert len(df) == 5
    assert set(df["PersonID"]) == {1, 2, 3, 4, 5}

    # Verify NULL birth year coerced to 0
    doe_row = df[df["PersonID"] == 5].iloc[0]
    assert doe_row["BirthYear"] == 0

    # Verify family context for James Depar (ID 2): wife Vide (ID 3) and child Lucretia (ID 4)
    james_row = df[df["PersonID"] == 2].iloc[0]
    rels = set(james_row["RelativesList"])
    assert "Vide Depar" in rels
    assert "Lucretia Depar" in rels


def test_extract_people_from_rm_handles_missing_family_tables(tmp_path):
    """If FamilyTable or ChildTable are absent or unreadable, extract_people_from_rm must catch
    the warning gracefully and still return primary individuals with empty RelativesList."""
    db_path = tmp_path / "broken_tree.rmtree"
    conn = sqlite3.connect(str(db_path))
    conn.create_collation("RMNOCASE", reg.rmnocase)
    conn.execute("""
        CREATE TABLE NameTable (
            OwnerID INTEGER, Given TEXT, Surname TEXT, BirthYear INTEGER, IsPrimary INTEGER
        )
    """)
    conn.execute("INSERT INTO NameTable VALUES (1, 'Jean', 'Gagnon', 1850, 1)")
    conn.commit()
    conn.close()

    df = reg.extract_people_from_rm(str(db_path))
    assert len(df) == 1
    assert df.iloc[0]["FullName"] == "Jean Gagnon"
    assert df.iloc[0]["RelativesList"] == []


# ==========================================
# find_fuzzy_duplicates TESTS
# ==========================================

def test_find_fuzzy_duplicates_empty_dataframe_returns_empty():
    """Passing an empty DataFrame to find_fuzzy_duplicates must return an empty DataFrame without erroring."""
    empty_df = pd.DataFrame(columns=["PersonID", "Given", "Surname", "FullName", "BirthYear", "RelativesList"])
    matches_df = reg.find_fuzzy_duplicates(empty_df)
    assert matches_df.empty


def test_find_fuzzy_duplicates_pass_one_matches_similar_names_within_age_gap():
    """Pass 1 should match candidates with token_set_ratio >= FUZZY_THRESHOLD (82)
    and age difference <= MAX_AGE_GAP (5)."""
    records = [
        {
            "PersonID": 1, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1815, "RelativesList": [],
        },
        {
            "PersonID": 2, "Given": "Joseph", "Surname": "Hougton",
            "FullName": "Joseph Hougton", "BirthYear": 1817, "RelativesList": [],
        },
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=False)

    assert len(matches_df) == 1
    row = matches_df.iloc[0]
    assert row["ID_1"] == 1
    assert row["ID_2"] == 2
    assert row["Age_Gap"] == 2
    assert row["Score"] >= reg.FUZZY_THRESHOLD


def test_find_fuzzy_duplicates_pass_one_ignores_beyond_max_age_gap():
    """Pass 1 must ignore pairs whose birth years differ by more than MAX_AGE_GAP (default 5 years),
    even if their full names match 100%."""
    records = [
        {
            "PersonID": 1, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1815, "RelativesList": [],
        },
        {
            "PersonID": 2, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1830, "RelativesList": [],
        },
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=False)
    assert matches_df.empty


def test_find_fuzzy_duplicates_skips_pass_two_by_default():
    """When run_pass_two=False, individuals with unknown birth years (BirthYear=0) must be skipped."""
    records = [
        {
            "PersonID": 1, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 0, "RelativesList": [],
        },
        {
            "PersonID": 2, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1815, "RelativesList": [],
        },
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=False)
    assert matches_df.empty


def test_find_fuzzy_duplicates_pass_two_matches_unknown_birth_year_with_strict_threshold():
    """When run_pass_two=True, records with unknown birth years (BirthYear=0)
    are matched using FUZZY_THRESHOLD_STRICT (95)."""
    records = [
        {
            "PersonID": 1, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 0, "RelativesList": [],
        },
        {
            "PersonID": 2, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1815, "RelativesList": [],
        },
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=True)

    assert len(matches_df) == 1
    row = matches_df.iloc[0]
    assert row["ID_1"] == 1
    assert row["ID_2"] == 2
    assert row["BirthYear_1"] == "Unknown"
    assert row["BirthYear_2"] == 1815


def test_find_fuzzy_duplicates_filters_out_family_conflicts():
    """If name similarity is high but both candidates have non-matching relatives, family_conflict
    causes the pair to be excluded from potential duplicate matches."""
    records = [
        {
            "PersonID": 1, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1815, "RelativesList": ["Mary Smith"],
        },
        {
            "PersonID": 2, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1816, "RelativesList": ["Sarah Johnson"],
        },
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=False)
    assert matches_df.empty


def test_find_fuzzy_duplicates_verifies_family_matches():
    """If candidates share at least one relative, Family_Verified should be set to True in the match record."""
    records = [
        {
            "PersonID": 1, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1815, "RelativesList": ["Mary Smith"],
        },
        {
            "PersonID": 2, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1816,
            "RelativesList": ["Mary Smith", "John Houghton"],
        },
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=False)

    assert len(matches_df) == 1
    assert bool(matches_df.iloc[0]["Family_Verified"]) is True


def test_find_fuzzy_duplicates_pass_two_prefilters_by_name_token_initial(monkeypatch):
    """PERF-4: Pass 2 must not score every unknown-age record against every record in
    the tree - it should prefilter to only those sharing a name-token initial letter
    first. A record whose name shares no initial letter with the unknown-age record
    should never reach fuzz.token_set_ratio at all."""
    records = [
        {
            "PersonID": 1, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 0, "RelativesList": [],
        },
        {
            "PersonID": 2, "Given": "Joseph", "Surname": "Houghton",
            "FullName": "Joseph Houghton", "BirthYear": 1815, "RelativesList": [],
        },
        {
            # Birth year kept far outside MAX_AGE_GAP of PersonID 2's 1815 so Pass 1's
            # sliding-window break also skips this pair - isolates the assertion to Pass 2.
            "PersonID": 3, "Given": "Zelda", "Surname": "Quimby",
            "FullName": "Zelda Quimby", "BirthYear": 1900, "RelativesList": [],
        },
    ]
    df = pd.DataFrame(records)

    scored_pairs = []
    real_ratio = reg.fuzz.token_set_ratio

    def counting_ratio(a, b):
        scored_pairs.append((a, b))
        return real_ratio(a, b)

    monkeypatch.setattr(reg.fuzz, "token_set_ratio", counting_ratio)

    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=True)

    assert len(matches_df) == 1
    assert not any("Quimby" in b or "Zelda" in b for _a, b in scored_pairs)


def test_find_fuzzy_duplicates_respects_test_limit():
    """Specifying test_limit caps the number of known-age records processed in Pass 1 and skips Pass 2."""
    records = [
        {
            "PersonID": i,
            "Given": "Joseph",
            "Surname": f"Houghton{i}",
            "FullName": f"Joseph Houghton{i}",
            "BirthYear": 1815 + (i % 2),
            "RelativesList": [],
        }
        for i in range(1, 20)
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=True, test_limit=2)
    # df is sorted by BirthYear before comparing; test_limit=2 only processes the first two records in sorted order
    sorted_known_ids = set(df[df["BirthYear"] > 0].sort_values("BirthYear").iloc[:2]["PersonID"])
    assert not matches_df.empty
    assert set(matches_df["ID_1"]).issubset(sorted_known_ids)


def test_find_fuzzy_duplicates_handles_missing_or_malformed_columns():
    """DataFrame missing expected non-essential values or having NaN birth years
    should handle coercion without crashing."""
    records = [
        {
            "PersonID": 1, "Given": None, "Surname": "Houghton",
            "FullName": "Houghton", "BirthYear": 1815, "RelativesList": [],
        },
        {
            "PersonID": 2, "Given": None, "Surname": "Houghton",
            "FullName": "Houghton", "BirthYear": 1815, "RelativesList": [],
        },
    ]
    df = pd.DataFrame(records)
    matches_df = reg.find_fuzzy_duplicates(df, run_pass_two=False)
    assert len(matches_df) == 1
