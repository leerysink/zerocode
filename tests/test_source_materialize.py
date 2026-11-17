# tests/test_source_materialize.py
"""快照落盘：模板仍在、快照在 frontend/public/source、README 溯源、越界路径被拦。

纯文件系统断言（tmp_path），不碰 DB 也不起容器；接缝级验证在 Task 5，端到端在 Task 9。

本文件全是同步用例，故不挂模块级 `pytestmark = pytest.mark.asyncio`：auto 模式下给
同步用例挂 asyncio 标记，每个用例多刷一条 PytestWarning（同门文件
tests/test_source_guard.py / test_source_hosting.py / test_source_ingest.py 同一裁定）。
"""
import json
import os
from pathlib import Path

import pytest

from app.source import materialize


def _fake_template(tmp_path: Path) -> Path:
    t = tmp_path / "tpl" / "static"
    (t / "frontend" / "src").mkdir(parents=True)
    (t / "frontend" / "package.json").write_text('{"name":"{{SLUG}}-frontend"}',
                                                  encoding="utf-8", newline="")
    (t / "frontend" / "src" / "App.vue").write_text("<template>{{APP_NAME}}</template>",
                                                     encoding="utf-8", newline="")
    (t / "README.md").write_text("# {{APP_NAME}}\n\n模板正文\n", encoding="utf-8", newline="")
    return t


def _payload(**over):
    # manifest 里的中文是真实形态，不是装饰：app/source/ingest.py:244-246 的 `files[].title`
    # 取的是中文文档的首个标题，`skipped[].path` 取的是仓库里的中文路径，Task 6/9 直接读
    # manifest.json 上这几个字。故这里必须按中文落盘（见 test_manifest_written_as_utf8_lf
    # 的正向字节断言）——夹具若全是 ASCII，ensure_ascii 的取值就永远测不出差别。
    m = {"schema": "zc-source/1", "host": "gitee", "owner": "leerys", "repo": "zerocode",
         "ref": "master", "sourceUrl": "https://gitee.com/leerys/zerocode",
         "fetchedAt": "2026-11-17T09:12:00+00:00", "subpath": None, "truncated": False,
         "counts": {"listed": 4, "snapshotted": 2, "skipped": 2},
         "files": [{"path": "README.md", "bytes": 3, "title": "T", "dir": ""},
                   {"path": "docs/a.md", "bytes": 3, "title": "部署指南", "dir": "docs"}],
         "skipped": [{"path": "图片/图标.png", "reason": "not-text"}]}
    m.update(over)
    return {"manifest": m, "files": {"README.md": b"# T\n", "docs/a.md": b"# A\n"}}


def test_write_snapshot_lands_under_frontend_public(tmp_path):
    materialize.write_snapshot(tmp_path, _payload())
    assert (tmp_path / "frontend/public/source/manifest.json").is_file()
    assert (tmp_path / "frontend/public/source/files/README.md").read_bytes() == b"# T\n"
    assert (tmp_path / "frontend/public/source/files/docs/a.md").read_bytes() == b"# A\n"


def test_manifest_written_as_utf8_lf(tmp_path):
    materialize.write_snapshot(tmp_path, _payload())
    raw = (tmp_path / "frontend/public/source/manifest.json").read_bytes()
    assert b"\r" not in raw, "换行必须是 LF"
    assert json.loads(raw.decode("utf-8"))["schema"] == "zc-source/1"
    # 正向钉中文：先有「字节里真的有这三个汉字」，才有资格说「没有被转义」。
    # 只否定 `\\u` 是空断言——纯 ASCII 载荷在 ensure_ascii=True/False 下字节完全一样。
    assert "指南".encode("utf-8") in raw, "中文标题必须按 UTF-8 原样落盘"
    assert "图片".encode("utf-8") in raw, "跳过清单里的中文路径同样不转义"
    assert "\\u" not in raw.decode("utf-8"), "中文不该被转义成 \\uXXXX"
    assert json.loads(raw.decode("utf-8"))["files"][1]["title"] == "部署指南"


def test_manifest_written_even_when_no_file_snapshotted(tmp_path):
    """一个文本都没收到也要落 manifest：前端按它判「快照为空」，缺文件会变成 404 白屏。"""
    payload = _payload()
    payload["files"] = {}
    payload["manifest"]["counts"]["snapshotted"] = 0
    payload["manifest"]["files"] = []
    materialize.write_snapshot(tmp_path, payload)
    assert (tmp_path / "frontend/public/source/manifest.json").is_file()


# 反斜杠与盘符形态只在 Windows 上有意义：NT 上 `files_dir / "C:/x.md"` 会整段替换成盘上的
# 绝对路径（posixpath 既不看它是绝对的、也不觉得它越界），只有写盘前的 relative_to 拦得住。
# 该形态在 POSIX 下只是个合法相对名（会老实落进 files/ 里），故按平台追加；跨平台都成立的
# 包含性证明见 test_absolute_path_cannot_land_outside_snapshot_files_dir。
_BAD_REMOTE_PATHS = ["../escape.md", "/abs.md", "a\\b.md"]
if os.name == "nt":
    _BAD_REMOTE_PATHS.append("C:/escape.md")


def _sandbox_entries(tmp_path: Path) -> list[str]:
    """沙箱内被创建出来的每一条路径（含空目录）——「拒一个形态必须零副作用」的度量。"""
    return sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))


@pytest.mark.parametrize("bad", _BAD_REMOTE_PATHS)
def test_unsafe_remote_path_is_rejected_not_written(tmp_path, bad):
    with pytest.raises(materialize.SnapshotError):
        materialize.write_snapshot(tmp_path, {"manifest": _payload()["manifest"],
                                              "files": {bad: b"x"}})
    # `files_dir / bad` 只在拼接结果仍留在沙箱内时才是可证的落点：绝对与盘符形态会被
    # pathlib 整段替换成沙箱外的路径（Windows 上 `/abs.md` 拼出盘根 C:\abs.md，本来就写
    # 不进去），那条断言在那两个形态上是空的，故只探沙箱内的形态；沙箱外的绝对形态由
    # 下面那条包含性用例（逃逸点选在 tmp_path 之内）负责。
    joined = (tmp_path / "frontend" / "public" / "source" / "files") / bad
    if joined.is_relative_to(tmp_path):
        assert not joined.exists()
    assert _sandbox_entries(tmp_path) == [], "预检失败也要零副作用"
    assert not (tmp_path / "frontend" / "public" / "source").exists(), "预检失败也要零副作用"


def test_absolute_path_cannot_land_outside_snapshot_files_dir(tmp_path):
    """绝对形态的包含性证明，两个平台跑的是同一条用例、逃逸点刻意选在沙箱之内。

    `tmp_path.as_posix()` 在 NT 上给出 `C:/.../outside.md`、在 POSIX 上给出
    `/tmp/.../outside.md`：两种拼法都会把 `files_dir / 该串` 整段替换成 files/ 目录外的
    绝对路径，而落点仍留在 tmp_path 里，于是「没有写到 files/ 之外」是沙箱内可证的。
    分工：POSIX 上 `_safe_rel` 的 `startswith("/")` 第一层就拦下；NT 上该串既无前导斜杠
    也无反斜杠、`posixpath.normpath` 与 `isabs` 都当它是相对名，**只有写盘前的
    relative_to 复核拦得住**（`C:/escape.md` 同属这一层唯一能拦的形态）。
    """
    with pytest.raises(materialize.SnapshotError):
        materialize.write_snapshot(tmp_path, {
            "manifest": _payload()["manifest"],
            "files": {f"{tmp_path.as_posix()}/outside.md": b"x"}})
    assert not (tmp_path / "outside.md").exists(), "绝对形态逃逸到了快照 files/ 之外"
    assert _sandbox_entries(tmp_path) == [], "拒一个形态必须零副作用"


def test_path_traversal_cannot_escape_snapshot_dir(tmp_path):
    """`docs/../oops.md` 归一后仍在快照目录内，但归一前的形态必须被拒。"""
    with pytest.raises(materialize.SnapshotError):
        materialize.write_snapshot(tmp_path, {"manifest": _payload()["manifest"],
                                              "files": {"docs/../../escape.md": b"x"}})
    # 光判抛错证明不了它没在别处落笔：`files_dir / "docs/../../escape.md"` 归一后落在
    # 快照根目录（files 的上一级），那里必须没有文件，整个沙箱也必须为空。
    assert not (tmp_path / "frontend" / "public" / "source" / "escape.md").exists()
    assert not (tmp_path / "frontend" / "public" / "escape.md").exists()
    assert _sandbox_entries(tmp_path) == [], "拒一个形态必须零副作用"


def test_readme_gets_provenance_section_and_keeps_template_text(tmp_path):
    """溯源段是**追加**：模板那份 README 写着本栈能力边界，覆盖等于删掉 AI 的起步说明。"""
    (tmp_path / "README.md").write_text("# 名字\n\n模板正文\n", encoding="utf-8", newline="")
    materialize.write_snapshot(tmp_path, _payload())
    materialize.append_readme_provenance(tmp_path, _payload()["manifest"])
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "模板正文" in text, "溯源段把模板 README 抹了"
    assert "https://gitee.com/leerys/zerocode" in text
    assert "frontend/public/source/" in text
    assert "manifest.json" in text
    # read_text 走 universal newlines，会把 CRLF 抹平成 \n，所以 `newline=""` 这条写契约
    # 只能在字节上钉：丢了它，Windows 上落盘的就是 CRLF，而上面四条断言照旧全绿。
    readme_bytes = (tmp_path / "README.md").read_bytes()
    assert b"\r" not in readme_bytes, "README 必须是纯 LF（write_text 要带 newline=\"\"）"
    assert "仓库源码快照".encode("utf-8") in readme_bytes


def test_provenance_without_readme_still_documents_snapshot(tmp_path):
    """没有模板 README 的工作区（纯快照铺底）也要留下一份「快照在哪、怎么用」的说明。"""
    materialize.append_readme_provenance(tmp_path, _payload()["manifest"])
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# 仓库源码快照")
    assert "frontend/public/source/manifest.json" in text
    # 兜底分支是另一句 write_text，newline="" 得单独钉一次。
    assert b"\r" not in (tmp_path / "README.md").read_bytes(), "兜底 README 同样必须纯 LF"


def test_compose_with_source_renders_then_writes(tmp_path):
    from app.deploy import scaffold
    tpl = _fake_template(tmp_path)
    root = tmp_path / "app"
    payload = _payload()
    fn = materialize.compose_with_source(
        lambda r, m: scaffold.render_layers([tpl], r, m, data_track=False), payload)
    fn(root, {"SLUG": "s1", "APP_NAME": "名字", "DEV_PORT": "5173", "PROD_PORT": "8080",
              "DEV_DB": "s1_dev", "PROD_DB": "s1_prod", "TODAY": "20261117"})
    assert json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))["name"] == "s1-frontend"
    assert (root / "frontend/public/source/files/docs/a.md").is_file()
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "名字" in readme and "manifest.json" in readme


def test_snapshot_body_with_placeholder_bytes_does_not_break_creation(tmp_path):
    """远端正文里带 `{{APP_NAME}}` 是常事（它本身就是模板仓库）：残留自检跑在渲染期，
    快照在渲染之后落盘，所以既不该被替换、也不该把创建打成 500。"""
    from app.deploy import scaffold
    tpl = _fake_template(tmp_path)
    payload = _payload()
    payload["files"]["docs/a.md"] = "{{APP_NAME}} 和 {{SLUG}} 原样留着".encode("utf-8")
    payload["manifest"]["files"][1]["bytes"] = len(payload["files"]["docs/a.md"])
    root = tmp_path / "app"
    fn = materialize.compose_with_source(
        lambda r, m: scaffold.render_layers([tpl], r, m, data_track=False), payload)
    fn(root, {"SLUG": "s1", "APP_NAME": "名字", "DEV_PORT": "5173", "PROD_PORT": "8080",
              "DEV_DB": "s1_dev", "PROD_DB": "s1_prod", "TODAY": "20261117"})
    body = (root / "frontend/public/source/files/docs/a.md").read_text(encoding="utf-8")
    assert "{{APP_NAME}}" in body
    assert "名字" in (root / "frontend/src/App.vue").read_text(encoding="utf-8")
