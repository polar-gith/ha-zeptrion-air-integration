"""Static checks run in CI: translations complete, versions consistent."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "zeptrion_air"
LANGUAGES = ("en", "de", "fr", "it")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: str(node)}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


STRINGS = _flatten(_load(COMPONENT / "strings.json"))


@pytest.mark.parametrize("language", LANGUAGES)
def test_translation_has_every_key(language: str) -> None:
    """Each translation has exactly the keys of strings.json, no text is empty."""
    translation = _flatten(_load(COMPONENT / "translations" / f"{language}.json"))
    assert sorted(set(STRINGS) - set(translation)) == [], "missing keys"
    assert sorted(set(translation) - set(STRINGS)) == [], "keys not in strings.json"
    assert [k for k, v in translation.items() if not v.strip()] == [], "empty texts"


@pytest.mark.parametrize("language", LANGUAGES)
def test_translation_keeps_placeholders(language: str) -> None:
    """Placeholders such as {hostname} are kept in every language."""
    translation = _flatten(_load(COMPONENT / "translations" / f"{language}.json"))
    for key, text in STRINGS.items():
        if text.startswith("[%key:"):
            continue
        assert set(PLACEHOLDER.findall(translation[key])) == set(PLACEHOLDER.findall(text)), key


def test_versions_are_consistent() -> None:
    """Manifest version YEAR.MONTH.PATCH; minimum HA in hacs.json and README agree."""
    manifest = _load(COMPONENT / "manifest.json")
    assert re.fullmatch(r"20\d\d\.(1[0-2]|[1-9])\.\d+", manifest["version"]), manifest["version"]
    minimum = _load(ROOT / "hacs.json")["homeassistant"]
    major_minor = ".".join(minimum.split(".")[:2])
    assert f"Requires Home Assistant {major_minor} or newer" in (ROOT / "README.md").read_text(
        encoding="utf-8"
    )


def test_services_are_documented() -> None:
    """Every action in services.yaml is named in the README."""
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [name for name in services if name not in readme]
    assert missing == []
