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
