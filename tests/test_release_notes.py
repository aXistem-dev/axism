from scripts.release_notes import main, section

CHANGELOG = """# Changelog

## [Unreleased]

## [0.2.0] — 2026-10-28

### Added

- New thing

## [0.1.2] — 2026-10-27

### Fixed

- Old thing
"""


def test_section_returns_body_without_heading():
    assert section(CHANGELOG, "0.2.0") == "### Added\n\n- New thing"


def test_section_accepts_tag_prefix():
    assert section(CHANGELOG, "v0.1.2") == "### Fixed\n\n- Old thing"


def test_section_missing_or_empty_is_blank():
    assert section(CHANGELOG, "9.9.9") == ""
    assert section(CHANGELOG, "Unreleased") == ""


def test_main_exit_codes(tmp_path, capsys):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    assert main(["release_notes.py", "v0.2.0", str(path)]) == 0
    assert "- New thing" in capsys.readouterr().out
    assert main(["release_notes.py", "v9.9.9", str(path)]) == 1
