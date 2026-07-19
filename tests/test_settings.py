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
