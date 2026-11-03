"""The versioning tool: a change is noticed, only the touched module moves, history is logged."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mm_under_test", ROOT / "tools/module_manifest.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "src/reggraph/alpha").mkdir(parents=True)
    (tmp_path / "src/reggraph/beta").mkdir(parents=True)
    (tmp_path / "src/reggraph/alpha/a.py").write_text("A = 1\n")
    (tmp_path / "src/reggraph/beta/b.py").write_text("B = 1\n")
    (tmp_path / "CHANGELOG.md").write_text("# log\n<!-- module-log -->\n")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "module_versions.json")
    monkeypatch.setattr(mod, "CHANGELOG", tmp_path / "CHANGELOG.md")
    monkeypatch.setattr(
        mod, "MODULES", {"alpha": ("src/reggraph/alpha",), "beta": ("src/reggraph/beta",)}
    )
    assert mod.init() == 0
    return mod


def test_clean_tree_passes(mm: ModuleType) -> None:
    assert mm.check() == 0


def test_edit_is_detected_for_that_module_only(mm: ModuleType) -> None:
    (mm.ROOT / "src/reggraph/alpha/a.py").write_text("A = 2\n")
    assert mm.changed_modules(mm.load()) == ["alpha"]
    assert mm.check() == 1


def test_bump_moves_only_the_touched_module_and_logs(mm: ModuleType) -> None:
    (mm.ROOT / "src/reggraph/alpha/a.py").write_text("A = 2\n")
    assert mm.bump("alpha", "minor", "new feature") == 0
    versions = {k: v["version"] for k, v in mm.load()["modules"].items()}
    assert versions == {"alpha": "0.2.0", "beta": "0.1.0"}
    assert "**alpha 0.2.0**" in (mm.ROOT / "CHANGELOG.md").read_text()
    assert mm.check() == 0


def test_deleting_and_adding_files_count_as_changes(mm: ModuleType) -> None:
    (mm.ROOT / "src/reggraph/beta/b.py").unlink()
    assert mm.changed_modules(mm.load()) == ["beta"]
    mm.bump("beta", "patch", "removed b")
    (mm.ROOT / "src/reggraph/beta/c.py").write_text("C = 1\n")
    assert mm.changed_modules(mm.load()) == ["beta"]


def test_file_outside_any_module_is_flagged(mm: ModuleType) -> None:
    (mm.ROOT / "src/reggraph/stray.py").write_text("X = 1\n")
    assert mm.uncovered_files() == ["src/reggraph/stray.py"]
    assert mm.check() == 1


def test_line_endings_do_not_change_the_fingerprint(mm: ModuleType) -> None:
    (mm.ROOT / "src/reggraph/alpha/a.py").write_bytes(b"A = 1\r\n")
    assert mm.check() == 0


@pytest.mark.parametrize(
    ("kind", "expected"), [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")]
)
def test_next_version(mm: ModuleType, kind: str, expected: str) -> None:
    assert mm._next("1.2.3", kind) == expected


def test_bump_requires_message_and_known_module(mm: ModuleType) -> None:
    assert mm.bump("alpha", "patch", "  ") == 2
    assert mm.bump("nope", "patch", "x") == 2


def test_add_registers_a_new_module_and_logs(
    mm: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    (mm.ROOT / "src/reggraph/gamma").mkdir()
    (mm.ROOT / "src/reggraph/gamma/g.py").write_text("G = 1\n")
    monkeypatch.setattr(
        mm,
        "MODULES",
        {**mm.MODULES, "gamma": ("src/reggraph/gamma",)},
    )
    assert mm.check() == 1  # listed in MODULES but not yet registered
    assert mm.add("gamma", "new module") == 0
    assert mm.load()["modules"]["gamma"]["version"] == "0.1.0"
    assert "**gamma 0.1.0**" in (mm.ROOT / "CHANGELOG.md").read_text()
    assert mm.check() == 0
    assert mm.add("gamma", "again") == 2  # cannot register twice
    assert mm.add("unknown", "x") == 2
