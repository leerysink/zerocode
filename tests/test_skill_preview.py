"""技能安装预览快照（ADR-0061 / ticket 004）：纯单元，monkeypatch data_dir，不碰 DB。"""
import json

import pytest

from app.skills import preview


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "data_dir", lambda: tmp_path)
    return tmp_path


def _payload():
    return {"name": "PDF", "description": "d",
            "files": {"SKILL.md": b"---\nname: PDF\ndescription: d\n---\nbody",
                      "scripts/run.py": b"print(1)", "assets/logo.png": b"\x89PNG"},
            "source_type": "github", "source_url": "https://x.invalid/r", "subpath": None}


def test_save_load_roundtrip(staging):
    token = preview.save_snapshot(1, _payload())
    p = preview.load_snapshot(1, token)
    assert p["name"] == "PDF"
    assert p["files"]["scripts/run.py"] == b"print(1)"
    assert p["source_type"] == "github"


def test_owner_isolation(staging):
    token = preview.save_snapshot(1, _payload())
    assert preview.load_snapshot(2, token) is None


def test_expiry(staging):
    token = preview.save_snapshot(1, _payload())
    meta = staging / "skill-preview" / token / "meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    m["expires_at"] = 0
    meta.write_text(json.dumps(m), encoding="utf-8")
    assert preview.load_snapshot(1, token) is None


def test_consume_removes(staging):
    token = preview.save_snapshot(1, _payload())
    d = staging / "skill-preview" / token
    p = preview.consume_snapshot(1, token)
    assert p is not None and not d.exists()
    assert preview.load_snapshot(1, token) is None


def test_token_traversal_rejected(staging):
    preview.save_snapshot(1, _payload())
    assert preview.load_snapshot(1, "../skill-preview") is None
    assert preview.load_snapshot(1, "short") is None
    assert preview.load_snapshot(1, None) is None


def test_manifest(staging):
    token = preview.save_snapshot(1, _payload())
    m = preview.snapshot_manifest(1, token)
    by = {f["path"]: f for f in m["files"]}
    assert by["scripts/run.py"]["kind"] == "text"
    assert by["assets/logo.png"]["kind"] == "image"
    assert "body" in m["skill_md"]
    assert m["name"] == "PDF"


def test_sweep_expired(staging):
    token = preview.save_snapshot(1, _payload())
    meta = staging / "skill-preview" / token / "meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    m["expires_at"] = 0
    meta.write_text(json.dumps(m), encoding="utf-8")
    preview.save_snapshot(1, _payload())   # 触发 sweep
    assert not (staging / "skill-preview" / token).exists()