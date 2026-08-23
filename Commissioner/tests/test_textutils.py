from Commissioner import textutils


def test_sanitize_quotes():
    assert textutils.sanitize_quotes("‘hello’ “world”") == "'hello' \"world\""
    assert textutils.sanitize_quotes("") == ""
    assert textutils.sanitize_quotes(None) == ""


def test_normalize_whitespace():
    assert textutils.normalize_whitespace("  hello   world \n \t ") == "hello world"
    assert textutils.normalize_whitespace("hello\xa0world") == "hello world"
    assert textutils.normalize_whitespace("") == ""
    assert textutils.normalize_whitespace(None) == ""


def test_clean_text():
    assert textutils.clean_text("  hello \u200b world\xa0 ") == "hello world"
    assert textutils.clean_text(None) == ""
    assert textutils.clean_text(123) == "123"
    assert textutils.clean_text("") == ""


def test_to_snake_case():
    assert textutils.to_snake_case("camelCase") == "camel_case"
    assert textutils.to_snake_case("PascalCase") == "pascal_case"
    assert textutils.to_snake_case("HTTPServer") == "http_server"
    assert textutils.to_snake_case("some-dashed-name") == "some_dashed_name"
    assert textutils.to_snake_case("some  spaced  name") == "some_spaced_name"
    assert textutils.to_snake_case("") == ""
    assert textutils.to_snake_case(None) == ""


def test_sanitize_image_filename():
    assert textutils.sanitize_image_filename("abc 123/def") == "abc_123_def.jpg"
    assert textutils.sanitize_image_filename("abc-123_DEF") == "abc-123_DEF.jpg"
    assert textutils.sanitize_image_filename("3:1:33S7-9YBJ-9PD7") == "3_1_33S7-9YBJ-9PD7.jpg"
    assert textutils.sanitize_image_filename("") == ""
    assert textutils.sanitize_image_filename(None) == ""
