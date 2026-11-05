from __future__ import annotations

from pathlib import Path

import pytest

from smelt import __version__
from smelt.cli import main
from smelt.rules.registry import build_rule_set


def test_install_without_policy_ships_every_rule_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["skill", "install"]) == 0

    skill = tmp_path / ".agents/skills/smelt"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "VERSION").read_text(encoding="utf-8").strip() == __version__
    for rule in build_rule_set().rules:
        page = skill / f"references/docs/rules/{rule.code}.md"
        assert rule.explain().fix in page.read_text(encoding="utf-8")
    assert (skill / "references/docs/cli.md").is_file()
    assert (skill / "references/docs/configuration.md").is_file()
    assert not (tmp_path / "smelt.yml").exists()
    assert __version__ in capsys.readouterr().out


def test_existing_skill_requires_force_and_replacement_removes_old_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / "custom/smelt"
    arguments = ["skill", "install", "--destination", str(destination)]
    assert main(arguments) == 0
    entrypoint = destination / "SKILL.md"
    entrypoint.write_text("local edits", encoding="utf-8")
    stale = destination / "references/removed.md"
    stale.write_text("obsolete", encoding="utf-8")
    sibling = destination.parent / "other/SKILL.md"
    sibling.parent.mkdir()
    sibling.write_text("keep", encoding="utf-8")

    assert main(arguments) == 2
    assert entrypoint.read_text(encoding="utf-8") == "local edits"
    assert stale.exists()

    assert main([*arguments, "--force"]) == 0
    assert "name: smelt" in entrypoint.read_text(encoding="utf-8")
    assert not stale.exists()
    assert sibling.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in destination.parent.iterdir()) == [
        "other",
        "smelt",
    ]


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_force_refuses_non_skill_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / "existing"
    if kind == "file":
        destination.write_text("keep", encoding="utf-8")
    else:
        destination.mkdir()
        (destination / "data").write_text("keep", encoding="utf-8")

    assert main(["skill", "install", "--destination", "existing", "--force"]) == 2
    preserved = destination if kind == "file" else destination / "data"
    assert preserved.read_text(encoding="utf-8") == "keep"


def test_failed_replacement_restores_old_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / ".agents/skills/smelt"
    destination.mkdir(parents=True)
    entrypoint = destination / "SKILL.md"
    entrypoint.write_text("original", encoding="utf-8")
    rename = Path.rename

    def fail_staged_rename(path: Path, target: str | Path) -> Path:
        if path.name == "skill":
            raise OSError("replacement failed")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staged_rename)

    assert main(["skill", "install", "--force"]) == 2
    assert entrypoint.read_text(encoding="utf-8") == "original"
    assert list(destination.parent.iterdir()) == [destination]


def test_install_refuses_symlink_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    original = tmp_path / "original"
    original.mkdir()
    (original / "SKILL.md").write_text("keep", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(original, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    assert main(["skill", "install", "--destination", "linked", "--force"]) == 2
    assert link.is_symlink()
    assert (original / "SKILL.md").read_text(encoding="utf-8") == "keep"
