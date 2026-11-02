from pathlib import Path

from axism.paths import (
    decode_project_slug,
    encode_project_slug,
    is_session_uuid,
    short_session_id,
)


def test_encode_slug_linux_style():
    assert encode_project_slug("/home/alice/src/demo") == "-home-alice-src-demo"


def test_encode_slug_macos_style():
    assert (
        encode_project_slug("/Users/alice/Projects/app")
        == "-Users-alice-Projects-app"
    )


def test_decode_roundtrip_display():
    slug = encode_project_slug("/home/alice/src/demo")
    assert decode_project_slug(slug) == "/home/alice/src/demo"


def test_uuid_helpers():
    sid = "11111111-1111-1111-1111-111111111111"
    assert is_session_uuid(sid)
    assert short_session_id(sid) == "11111111"
    assert not is_session_uuid("not-a-uuid")


def test_config_root_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    from axism.paths import config_root

    assert config_root() == tmp_path.resolve()


def test_strict_probes_report_absence_and_surface_locked_folders(tmp_path: Path):
    import os

    import pytest

    from axism.paths import exists_strict, is_file_strict

    f = tmp_path / "state.db"
    f.write_text("x", encoding="utf-8")
    assert is_file_strict(f) and exists_strict(f)
    assert not is_file_strict(tmp_path) and exists_strict(tmp_path)
    assert not is_file_strict(tmp_path / "nope") and not exists_strict(tmp_path / "nope")
    # A path below a file is absent, not an error.
    assert not exists_strict(f / "child")

    gate = tmp_path / "gate"
    inner = gate / "inner"
    inner.mkdir(parents=True)
    (inner / "state.db").write_text("x", encoding="utf-8")
    gate.chmod(0)
    try:
        if os.access(gate, os.R_OK | os.X_OK):
            pytest.skip("permissions are not enforced here (running as root)")
        # Python 3.14's Path.exists/is_file return False here; these raise on every version.
        with pytest.raises(PermissionError):
            exists_strict(inner)
        with pytest.raises(PermissionError):
            is_file_strict(inner / "state.db")
    finally:
        gate.chmod(0o755)
