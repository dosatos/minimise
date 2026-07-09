import pytest

from minimise.personas import load_builtin_personas, load_personas


def test_missing_file_returns_builtins_only(tmp_path):
    # No user personas.yaml -> only the shipped built-ins.
    assert load_personas(tmp_path) == load_builtin_personas()


def test_inline_prompt(tmp_path):
    (tmp_path / "personas.yaml").write_text(
        "coder:\n  prompt: you are a coder\n"
    )
    personas = load_personas(tmp_path)
    assert personas["coder"].system_prompt == "you are a coder"
    assert personas["coder"].model is None


def test_builtin_doc_review_resolves_with_zero_config(tmp_path):
    personas = load_personas(tmp_path)
    for p in ("rigor", "consistency", "density", "clarity"):
        assert f"mini:doc-review:{p}" in personas
        # bare alias == v1 (latest)
        assert personas[f"mini:doc-review:{p}"].system_prompt == \
            personas[f"mini:doc-review:{p}@v1"].system_prompt
        assert personas[f"mini:doc-review:{p}"].model is None
    assert "ANALYTICAL RIGOR" in personas["mini:doc-review:rigor"].system_prompt


def test_versioned_key_resolves(tmp_path):
    personas = load_personas(tmp_path)
    assert "STRUCTURAL CLARITY" in personas["mini:doc-review:clarity@v1"].system_prompt


def test_version_key_natural_sort():
    from minimise.personas import _version_key
    # highest vN wins; v10 > v2 numerically (not lexically)
    assert max(["v1", "v2", "v10"], key=_version_key) == "v10"


def test_user_mini_namespace_rejected(tmp_path):
    (tmp_path / "personas.yaml").write_text(
        "mini:doc-review:rigor:\n  prompt: hijack\n"
    )
    with pytest.raises(ValueError, match="reserved"):
        load_personas(tmp_path)


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
