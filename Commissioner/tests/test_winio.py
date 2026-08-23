from Commissioner import winio


def test_move_with_retry(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello", encoding="utf-8")
    status = winio.move_with_retry(src, dst)
    assert status == "moved"
    assert dst.read_text(encoding="utf-8") == "hello"
    assert not src.exists()


def test_move_with_retry_skip_collision(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    status = winio.move_with_retry(src, dst, on_collision="skip")
    assert status == "skipped"
    assert dst.read_text(encoding="utf-8") == "old"
    assert not src.exists()


def test_read_text_with_retry(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("content", encoding="utf-8")
    assert winio.read_text_with_retry(f) == "content"


def test_unlink_with_retry(tmp_path):
    f = tmp_path / "to_delete.txt"
    f.write_text("bye", encoding="utf-8")
    winio.unlink_with_retry(f)
    assert not f.exists()


def test_replace_with_retry(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("updated", encoding="utf-8")
    dst.write_text("initial", encoding="utf-8")
    winio.replace_with_retry(src, dst)
    assert dst.read_text(encoding="utf-8") == "updated"
    assert not src.exists()
