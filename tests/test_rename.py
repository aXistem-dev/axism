from pathlib import Path

from axism.discover import discover_all
from axism.rename import rename_session

SESSION_A = "11111111-1111-1111-1111-111111111111"
DEMO_SLUG = "-home-alice-src-demo"


def test_rename_appends_custom_title(fake_root: Path):
    ok, msg = rename_session(SESSION_A, "Renamed Demo", root=fake_root)
    assert ok is True
    assert "Renamed Demo" in msg
    path = fake_root / "projects" / DEMO_SLUG / f"{SESSION_A}.jsonl"
    text = path.read_text(encoding="utf-8")
    assert '"type": "custom-title"' in text or '"type":"custom-title"' in text
    assert "Renamed Demo" in text
    projects = discover_all(fake_root)
    sess = next(s for s in projects[0].sessions if s.session_id == SESSION_A)
    assert sess.display_title == "Renamed Demo"


def test_rename_rejects_empty(fake_root: Path):
    ok, msg = rename_session(SESSION_A, "   ", root=fake_root)
    assert ok is False
    assert "empty" in msg.lower()


def test_rename_after_torn_last_line(fake_root):
    from axism.discover import find_session
    from axism.rename import rename_session

    _p, sess = find_session("11111111", fake_root)
    with sess.transcript_path.open("a", encoding="utf-8") as fh:
        fh.write('{"type":"user","mess')
    ok, _msg = rename_session(sess.session_id, "Fresh", root=fake_root)
    assert ok
    _p, again = find_session("11111111", fake_root)
    assert again.title == "Fresh"
