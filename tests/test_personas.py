import pytest

from minimise.personas import load_personas


def test_missing_file_returns_empty(tmp_path):
    assert load_personas(tmp_path) == {}


def test_inline_prompt(tmp_path):
    (tmp_path / "personas.yaml").write_text(
        "coder:\n  prompt: you are a coder\n"
    )
    personas = load_personas(tmp_path)
    assert personas["coder"].system_prompt == "you are a coder"
    assert personas["coder"].model is None


def test_prompt_file_relative(tmp_path):
    (tmp_path / "coder.md").write_text("file-based prompt")
    (tmp_path / "personas.yaml").write_text(
        "coder:\n  prompt_file: coder.md\n  model: opus\n"
    )
    personas = load_personas(tmp_path)
    assert personas["coder"].system_prompt == "file-based prompt"
    assert personas["coder"].model == "opus"


def test_prompt_file_missing(tmp_path):
    (tmp_path / "personas.yaml").write_text(
        "coder:\n  prompt_file: nope.md\n"
    )
    with pytest.raises(ValueError):
        load_personas(tmp_path)


def test_both_prompt_and_prompt_file(tmp_path):
    (tmp_path / "personas.yaml").write_text(
        "coder:\n  prompt: inline\n  prompt_file: coder.md\n"
    )
    with pytest.raises(ValueError):
        load_personas(tmp_path)


def test_neither_prompt_nor_prompt_file(tmp_path):
    (tmp_path / "personas.yaml").write_text(
        "coder:\n  model: opus\n"
    )
    with pytest.raises(ValueError):
        load_personas(tmp_path)
