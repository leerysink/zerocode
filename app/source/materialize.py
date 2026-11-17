# app/source/materialize.py
"""把内存快照落成工作区文件：模板照常渲染，快照落在 frontend/public/source/。

为什么落点是 frontend/public/source/：Vite 把 `public/` 原样拷进 `dist`，dev 预览与发布后的
nginx 静态站点都能按 `/source/**` 直出（static 栈的 try_files 回落只兜真实文件不存在的情况）。
于是「站点读自己的快照、按 manifest 往下钻取」既不用后端、也不用 router、不用改 nginx 配置。
先例是 app/templates/vue3-springboot/frontend/public/。

接缝形状抄 app/store/install.py 的 _materializer：铺源逻辑换掉，端口/app.json/回滚仍归
create_app_project 一处，别在这儿另起一套。

顺序不能调（base_render → write_snapshot → append_readme_provenance）：scaffold.render 的
占位残留自检扫的是整棵产物树的 `{{UPPER_SNAKE}}`，而远端正文里带 `{{APP_NAME}}` 是常事
（它本身就是模板仓库）——快照写在渲染之前，就会被自检打成「渲染残留」，一次粘贴把创建弄成
500；写在之后，正文里的占位字节既不被替换也不报错。

README 是追加不是覆盖：模板那份写着本栈能力边界（static 的「没有后端、没有数据库」），
抹掉它等于把 AI 的起步说明书删了。

落盘侧自己再判一遍路径形态，不是重复 guard/ingest 的功：那两处判的是「能不能拼进 URL」，
这里判的是「能不能拼进 Path」——`Path(files_dir) / "C:/x.md"` 在 Windows 上会整段替换成
盘上的绝对路径，posixpath 看不出它越界，只有写盘前的 relative_to 拦得住。预检排在任何
mkdir/write 之前，所以拒一个形态是零副作用的。

本模块不出网、不碰 DB：载荷由 app/source/ingest.py 备好后交进来，失败一律 SnapshotError，
由 create_app_project 的回滚兜住。预算与白名单也在那边判完，这里不另设上限。
"""
from __future__ import annotations

import json
import posixpath
from pathlib import Path
from typing import Callable
from urllib.parse import quote

SOURCE_ROOT_PARTS = ("frontend", "public", "source")
MANIFEST_REL = "/".join(SOURCE_ROOT_PARTS + ("manifest.json",))
FILES_REL_PREFIX = "/".join(SOURCE_ROOT_PARTS + ("files",)) + "/"
# 站点侧的读路径前缀：Vite 把 `frontend/public/` 原样拷进 dist 根，所以快照正文的公开地址是
# `/source/files/<编码后的落盘名>`。它与写侧的 FILES_REL_PREFIX 是同一条契约的两端（规格 §2.2/§5.3），
# 故编码规则放在这里 —— 谁改落点，谁就得同时改这条读路径。
FILES_URL_PREFIX = "/source/files/"


def snapshot_file_url(path: str) -> str:
    """`manifest.files[].url`：页面可直接 fetch 的正文地址（机器字段）。

    为什么必须有这个字段而不是让站点读 `files[].path`：写盘侧落的是 `_safe_rel` 归一后的名字，
    上游 raw 端点又按 `quote(path)` 编码（`c#1.md` → `c%231.md`），唯独 manifest 存的是原串——
    于是 `#` 会让 fetch 掉进 URL 片段（静默 404）、`%XX` 会被再解一次码而打到**另一篇**快照正文。
    三段口径（原样展示 / 落盘名 / fetch 目标）在这里对齐成一条：先按落盘侧的同一个 `normpath` 归一，
    再**逐段**百分号编码（`/` 只作分隔符保留），拼上公开前缀。
    `files[].path` 原样保留给人看，`url` 才是拿去发请求的那个。
    """
    return FILES_URL_PREFIX + "/".join(quote(seg) for seg in posixpath.normpath(path).split("/"))


_PROVENANCE = """

## 仓库源码快照（{source_url}）

- 来源：{host}/{owner}/{repo}@{ref}{subpath_note}　抓取时间：{fetched_at}
- 树里 {listed} 条，进快照 {snapshotted} 篇文本，跳过 {skipped} 条（清单见 manifest）
- 正文：`frontend/public/source/manifest.json` 的 `files[].url`（形如 `/source/files/<百分号编码路径>`，
  这是给页面 fetch 的**机器字段**）；`files[].path` 是仓库原路径，只用于展示——直接拿它拼 fetch，
  带 `#`/`?`/`%` 的那几篇会 404 或打到别的正文上
- 索引：`frontend/public/source/manifest.json`，正文按同名落在 `frontend/public/source/files/` 下
- 发布后 `/source/manifest.json` 与每个 `files[].url` 都直接可取，页面自己 fetch 就能做钻取
- 这是**构建期快照**，不是实时代理：仓库更新了要重建应用，页面里不要写「正在读 Gitee」
- 做官网/文档站请从这里取真正文，别自己编目录；标题与分组读 manifest 的 `files[].title/dir`
"""


class SnapshotError(Exception):
    """快照落盘失败；由 create_app_project 的回滚兜住，最终映射成 400。"""


def _safe_rel(path: str) -> str:
    """远端路径归一后必须仍在快照目录内：越界、绝对、反斜杠形态一律拒。

    远端树是不可信输入，`docs/../../x` 归一后会往上爬，绝对路径会直接指到别人家目录。
    """
    if not path or path.startswith("/") or "\\" in path:
        raise SnapshotError(f"快照路径形态非法: {path!r}")
    norm = posixpath.normpath(path)
    if norm.startswith("..") or norm == "." or posixpath.isabs(norm):
        raise SnapshotError(f"快照路径越界: {path!r}")
    return norm


def write_snapshot(root: Path, payload: dict) -> None:
    base = Path(root).joinpath(*SOURCE_ROOT_PARTS)
    files_dir = base / "files"
    for path in payload.get("files") or {}:
        rel = _safe_rel(path)
        target = files_dir / rel
        try:
            target.relative_to(files_dir)
        except ValueError:
            raise SnapshotError(f"快照路径越界: {path!r}") from None
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(
        json.dumps(payload["manifest"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="")
    for path, data in (payload.get("files") or {}).items():
        target = files_dir / _safe_rel(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def append_readme_provenance(root: Path, manifest: dict) -> None:
    readme = Path(root) / "README.md"
    section = _PROVENANCE.format(
        source_url=manifest["sourceUrl"], host=manifest["host"], owner=manifest["owner"],
        repo=manifest["repo"], ref=manifest["ref"],
        subpath_note=f"（子目录 {manifest['subpath']}）" if manifest.get("subpath") else "",
        fetched_at=manifest["fetchedAt"], listed=manifest["counts"]["listed"],
        snapshotted=manifest["counts"]["snapshotted"], skipped=manifest["counts"]["skipped"])
    if readme.is_file():
        original = readme.read_text(encoding="utf-8")
        readme.write_text(original.rstrip("\n") + "\n" + section, encoding="utf-8", newline="")
    else:
        readme.write_text(f"# 仓库源码快照\n{section}", encoding="utf-8", newline="")


def compose_with_source(base_render, payload: dict) -> Callable[[Path, dict], None]:
    """基础铺源（模板渲染或商店包复制）跑完后追加快照落盘与 README 溯源段。"""
    def materialize(root: Path, mapping: dict) -> None:
        base_render(root, mapping)
        write_snapshot(root, payload)
        append_readme_provenance(root, payload["manifest"])
    return materialize
