"""preview_page 截图的落盘、按会话归属授权与随会话清理（规格 2026-10-31 §7）。

截图不进数据库：文件名是唯一句柄，读取接口靠 chat_session 的属主判定授权。所以这里要钉的
不是「图片好不好看」，而是三件事——上限会不会涨爆磁盘、别人的会话能不能读到、删会话时目录
有没有跟着走。
"""
import os
import struct
import time
from pathlib import Path

import pytest

from app.services import browser_shot_service as shots

PAD = 64


def _png(width=1280, height=800, pad=PAD):
    """够骗过 png_size 的最小 PNG 头：魔数 + IHDR 长度 + IHDR + 宽高。"""
    return (shots.PNG_MAGIC + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x00" * pad)


def _touch_old(path, minutes_ago):
    t = time.time() - minutes_ago * 60
    os.utime(path, (t, t))


# ---------------------------------------------------------------- 落盘与上限


def test_save_writes_file_and_reports_dimensions(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    data = _png()
    row = shots.save(7, data)
    assert row == {"filename": row["filename"], "bytes": len(data),
                   "width": 1280, "height": 800}
    assert row["filename"].endswith(".png")
    path = shots.session_dir(7) / row["filename"]
    assert path.is_file() and path.read_bytes() == data
    assert shots.resolve(7, row["filename"]) == path


def test_save_rejects_empty_non_png_and_oversize(tmp_path, monkeypatch):
    """入口必须校验魔数：这不是洁癖，而是读取接口按 image/png 直发文件内容。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    for bad in (b"", b"\x00" * 64, shots.PNG_MAGIC + b"\x00" * 8):
        with pytest.raises(shots.ShotError):
            shots.save(7, bad)
    monkeypatch.setattr(shots, "MAX_SHOT_BYTES", 32)
    with pytest.raises(shots.ShotError, match="MB"):
        shots.save(7, _png())
    assert not shots.session_dir(7).exists(), "校验失败前不该建出会话目录"


def test_prune_evicts_oldest_by_count(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(shots, "MAX_SHOTS_PER_SESSION", 3)
    d = shots.session_dir(7)
    d.mkdir(parents=True)
    names = []
    for i in range(3):
        p = d / f"shot-{i}.png"
        p.write_bytes(_png())
        _touch_old(p, 300 - i * 60)
        names.append(p.name)

    row = shots.save(7, _png())
    left = sorted(p.name for p in d.glob("*.png"))
    assert names[0] not in left, "淘汰从最旧开始，最先那张必须走"
    assert row["filename"] in left and len(left) == 3


def test_prune_evicts_oldest_by_total_bytes(tmp_path, monkeypatch):
    """条数上限之外还有体积上限：20 张 5MB 是 100MB，演示机的 data/ 不该被这个吃掉。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    data = _png()
    monkeypatch.setattr(shots, "MAX_SHOTS_PER_SESSION", 100)
    monkeypatch.setattr(shots, "MAX_SESSION_BYTES", len(data) * 2 + 10)
    d = shots.session_dir(7)
    d.mkdir(parents=True)
    old, newer = d / "a.png", d / "b.png"
    old.write_bytes(data)
    newer.write_bytes(data)
    _touch_old(old, 60)
    _touch_old(newer, 5)

    shots.save(7, data)
    assert not old.exists() and newer.exists()


@pytest.mark.parametrize("name", [
    "", "x.txt", ".hidden.png", "-lead.png", "a/../x.png", "..\\x.png",
    "x.png/../y.png", "shot 1.png", "shot;rm.png",
])
def test_resolve_rejects_unsafe_or_missing_names(tmp_path, monkeypatch, name):
    """文件名是用户可改的 URL 段：正则 + 「取 basename」两道，不给目录穿越留缝。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    row = shots.save(7, _png())
    assert shots.resolve(7, row["filename"]) is not None
    assert shots.resolve(7, name) is None


def test_resolve_isolated_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    row = shots.save(7, _png())
    assert shots.resolve(8, row["filename"]) is None


def test_remove_session_dir_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    shots.save(7, _png())
    shots.remove_session_dir(7)
    assert not shots.session_dir(7).exists()
    shots.remove_session_dir(7)          # 删会话时目录本来就可能不存在，不能因此报错
    shots.remove_session_dir(9)


# ---------------------------------------------------------------- 读取接口


async def _mk_session(client, root):
    Path(root).mkdir(parents=True, exist_ok=True)      # 工作区接口要求目录已存在
    ws = (await client.post("/api/workspaces", json={"root_path": str(root)})).json()
    return (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()


async def test_owner_reads_shot_with_safe_headers(two_auth_clients, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    alice, _bob = two_auth_clients
    sess = await _mk_session(alice, tmp_path / "ws")
    data = _png()
    row = shots.save(sess["id"], data)

    got = await alice.get(f"/api/browser/shots/{sess['id']}/{row['filename']}")
    assert got.status_code == 200
    assert got.content == data
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "private" in got.headers["cache-control"]
    assert "immutable" in got.headers["cache-control"]


async def test_shot_read_is_isolated_and_404s(two_auth_clients, tmp_path, monkeypatch):
    """跨账号、缺文件、坏文件名一律 404：不给「存在但不属于你」留成 403 的探测面。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    alice, bob = two_auth_clients
    sess = await _mk_session(alice, tmp_path / "ws-a")
    row = shots.save(sess["id"], _png())
    other = await _mk_session(bob, tmp_path / "ws-b")

    assert (await bob.get(f"/api/browser/shots/{sess['id']}/{row['filename']}")).status_code == 404
    assert (await alice.get(f"/api/browser/shots/{other['id']}/nope.png")).status_code == 404
    assert (await alice.get(f"/api/browser/shots/{sess['id']}/..%2Fx.png")).status_code == 404
    assert (await alice.get(f"/api/browser/shots/{sess['id']}/missing.png")).status_code == 404


async def test_anon_cannot_read_shots(anon_client, tmp_path, monkeypatch):
    """全局 guard 覆盖这条路由：不登录连 404 都拿不到，直接 401。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    r = await anon_client.get("/api/browser/shots/1/whatever.png")
    assert r.status_code == 401


async def test_session_delete_removes_shot_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path / "ws")
    shots.save(sess["id"], _png())
    assert shots.session_dir(sess["id"]).is_dir()

    r = await client.delete(f"/api/sessions/{sess['id']}")
    assert r.status_code == 200, r.text
    assert not shots.session_dir(sess["id"]).exists(), "删会话留下孤儿截图目录 = 磁盘只涨不消"
