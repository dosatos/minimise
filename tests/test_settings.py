from minimise.settings import Settings, load_settings

import pytest


def test_defaults(tmp_path):
    assert load_settings(tmp_path) == Settings(harness="claude")


def test_from_file(tmp_path):
    (tmp_path / "settings.yaml").write_text("version: 0.0.1\nharness: pi\n")
    assert load_settings(tmp_path) == Settings(harness="pi")


def test_missing_file(tmp_path):
    assert load_settings(tmp_path / "does-not-exist") == Settings(harness="claude")


def test_empty_file_rejected(tmp_path):
    (tmp_path / "settings.yaml").write_text("")
    with pytest.raises(ValueError, match="version"):
        load_settings(tmp_path)


def test_wrong_version_rejected(tmp_path):
    (tmp_path / "settings.yaml").write_text("version: 9.9.9\n")
    with pytest.raises(ValueError, match="version"):
        load_settings(tmp_path)


def test_extra_keys_ignored(tmp_path):
    (tmp_path / "settings.yaml").write_text("version: 0.0.1\nharness: claude\nunknown_key: 42\n")
    assert load_settings(tmp_path) == Settings(harness="claude")


def test_model_defaults_to_none(tmp_path):
    """model is None when key is missing from settings.yaml."""
    (tmp_path / "settings.yaml").write_text("version: 0.0.1\nharness: claude\n")
    settings = load_settings(tmp_path)
    assert settings.model is None


def test_model_from_file(tmp_path):
    """model is read from settings.yaml when present."""
    (tmp_path / "settings.yaml").write_text("version: 0.0.1\nharness: claude\nmodel: claude-sonnet-4-8\n")
    settings = load_settings(tmp_path)
    assert settings.model == "claude-sonnet-4-8"


def test_model_none_when_missing_file(tmp_path):
    """Missing settings.yaml returns Settings with model=None."""
    settings = load_settings(tmp_path / "does-not-exist")
    assert settings.model is None
    assert settings == Settings()


# ---- prices: an optional section for the web's cost estimate ----------------

_PRICES = """version: 0.0.1
prices:
  as_of: 2026-10-26
  models:
    claude-sonnet-5-5: {input: 3, cache_read: 0.3, cache_write: 6, output: 15}
    default:           {input: 1, cache_read: 0.1, cache_write: 2, output: 5}
"""


def _write(tmp_path, text):
    (tmp_path / "settings.yaml").write_text(text)
    return tmp_path


def test_no_prices_section_means_no_prices(tmp_path):
    assert load_settings(_write(tmp_path, "version: 0.0.1\n")).prices is None
    assert load_settings(_write(tmp_path, "version: 0.0.1\nprices:\n")).prices is None
    assert Settings().prices is None


def test_prices_parsed(tmp_path):
    from minimise.settings import Prices

    settings = load_settings(_write(tmp_path, _PRICES))

    assert settings.prices == Prices(as_of="2026-10-26", models={
        "claude-sonnet-5-5": {"input": 3, "cache_read": 0.3, "cache_write": 6, "output": 15},
        "default": {"input": 1, "cache_read": 0.1, "cache_write": 2, "output": 5},
    })
    assert settings.harness == "claude"


def test_prices_as_of_parsed_as_a_date_is_stored_as_its_iso_string(tmp_path):
    import yaml

    assert not isinstance(yaml.safe_load(_PRICES)["prices"]["as_of"], str)  # YAML made a date
    assert load_settings(_write(tmp_path, _PRICES)).prices.as_of == "2026-10-26"
    quoted = _PRICES.replace("as_of: 2026-10-26", 'as_of: "Oct 2026"')
    assert load_settings(_write(tmp_path, quoted)).prices.as_of == "Oct 2026"


def test_prices_missing_as_of_rejected(tmp_path):
    _write(tmp_path, _PRICES.replace("  as_of: 2026-10-26\n", ""))
    with pytest.raises(ValueError, match=r"prices\.as_of"):
        load_settings(tmp_path)


def test_prices_missing_bucket_rejected(tmp_path):
    _write(tmp_path, _PRICES.replace("cache_write: 2, ", ""))
    with pytest.raises(ValueError, match=r"prices\.models\.default\.cache_write"):
        load_settings(tmp_path)


@pytest.mark.parametrize("value", ["-1", "-0.5", "cheap", "true", ".nan", "null"])
def test_prices_bad_price_rejected(tmp_path, value):
    _write(tmp_path, _PRICES.replace("output: 15", f"output: {value}"))
    with pytest.raises(ValueError, match=r"prices\.models\.claude-sonnet-5-5\.output"):
        load_settings(tmp_path)


@pytest.mark.parametrize("prices, key", [
    ("prices: 3\n", r"prices "),
    ("prices:\n  as_of: 2026-10-26\n", r"prices\.models"),
    ("prices:\n  as_of: 2026-10-26\n  models: {}\n", r"prices\.models"),
    ("prices:\n  as_of: 2026-10-26\n  models:\n    default: 3\n", r"prices\.models\.default"),
])
def test_prices_bad_shape_rejected(tmp_path, prices, key):
    _write(tmp_path, "version: 0.0.1\n" + prices)
    with pytest.raises(ValueError, match=key):
        load_settings(tmp_path)


def test_load_prices_reads_the_file_every_call(tmp_path):
    from minimise.settings import load_prices

    assert load_prices(None) is None  # no config dir: no prices
    assert load_prices(tmp_path) is None  # no settings.yaml
    _write(tmp_path, _PRICES)
    assert load_prices(tmp_path).models["default"]["output"] == 5
    _write(tmp_path, _PRICES.replace("output: 5", "output: 7"))
    assert load_prices(tmp_path).models["default"]["output"] == 7
