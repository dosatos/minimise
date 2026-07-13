"""The .claude-plugin manifests must stay in sync with the package and the skills."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
MARKETPLACE = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())


def _pyproject_version() -> str:
    try:
        import tomllib

        return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    except ImportError:  # ponytail: py3.9 has no tomllib; the version line is regular enough
        match = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
        assert match, "no version in pyproject.toml"
        return match.group(1)


def test_versions_agree():
    assert PLUGIN["version"] == _pyproject_version()
    assert [p["version"] for p in MARKETPLACE["plugins"]] == [_pyproject_version()]


def test_plugin_paths_exist():
    paths = [v for v in PLUGIN.values() if isinstance(v, str) and v.startswith("./")]
    assert paths, "plugin.json references no component paths"
    for path in paths:
        assert (ROOT / path).exists(), f"plugin.json references missing path: {path}"


def test_skill_names_match_their_directory():
    skills = sorted((ROOT / PLUGIN["skills"]).iterdir())
    assert skills, "no skills found"
    for skill_dir in skills:
        frontmatter = (skill_dir / "SKILL.md").read_text()
        name = re.search(r"^name:\s*(.+?)\s*$", frontmatter, re.M).group(1)
        assert name == skill_dir.name, f"{skill_dir.name}: SKILL.md name is {name!r}"
        assert re.fullmatch(r"[a-z0-9-]+", name), f"{name!r} is not a valid skill name"
