import sqlite3
from Commissioner.sqlite_helpers import rmnocase, register_rmnocase


def test_rmnocase_collation():
    assert rmnocase("abc", "ABC") == 0
    assert rmnocase("abc", "def") == -1
    assert rmnocase("def", "abc") == 1
    assert rmnocase(None, "") == 0
    assert rmnocase("a", None) == 1
    assert rmnocase(None, "a") == -1


def test_register_rmnocase():
    conn = sqlite3.connect(":memory:")
    register_rmnocase(conn)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE test (val TEXT COLLATE RMNOCASE)")
    cursor.executemany("INSERT INTO test (val) VALUES (?)", [("beta",), ("Alpha",), ("gamma",)])
    cursor.execute("SELECT val FROM test ORDER BY val COLLATE RMNOCASE")
    rows = [r[0] for r in cursor.fetchall()]
    assert rows == ["Alpha", "beta", "gamma"]
