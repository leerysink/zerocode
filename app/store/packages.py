# app/store/packages.py
"""不可变版本包（规格 M3-a §4）。

包必须是从未是活目录的物理 copy：上架后作者继续让 AI 改代码，不能波及已上架版本——
`run.sh` 的 checksum 闸门只保护作者自己的库，保护不了已分发出去的包。
"""
import hashlib, json, os, shutil
from datetime import datetime, timezone
from pathlib import Path
from app.deploy.errors import DeployError
from app.store import anchor as anchor_mod

SCHEMA = "zc-release/1"
# 顶层不进 src 的：平台生成物（deploy 含明文凭据）、独立进包的 sqlchange、清单与说明
EXCLUDE_TOP = {"deploy", "sqlchange", "app.json", "README.md"}
EXCLUDE_DIRS = {"node_modules", ".venv", "dist", "__pycache__", ".pytest_cache", ".vite",
                "venv", "build"}
# 大型栈 dev 预览就地跑 mvn：backend/target 与 backend/logs 直接落在应用工作区里。不收它们，
# 每预览一次 source_hash 就变一次（分叉判定会误报），包体还要背上几百 MB 编译产物。
# 按路径尾屏蔽而非裸名：target / logs 都是业务通用词（目标管理、日志页面），按名屏蔽会把作者的
# src/views/target 一类目录静默排除出包与指纹。落点由「dev 容器把 ../backend 挂成 working_dir」与模板里
# logging.path 的默认值共同决定，两者任一变动（拆多模块、改日志目录）都要回来对一眼这两份名单。
# uploads/ 刻意不在这里：上传件算不算包内容是个产品口径，没定下来前不悄悄替它做决定。
EXCLUDE_PATH_TAILS = (("backend", "target"), ("backend", "logs"))
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}


def _has_tail(parts: tuple, tail: tuple) -> bool:
    """路径里是否连续地出现这段目录序列（文件在更深处，所以不是「以…结尾」）。"""
    n = len(tail)
    return any(parts[i:i + n] == tail for i in range(len(parts) - n + 1))


def _is_excluded(parts: tuple) -> bool:
    """某个文件是否不进包也不进 source_hash（三个按名名单 + 一个按路径尾的构建产物名单）。"""
    return (parts[0] in EXCLUDE_TOP or any(part in EXCLUDE_DIRS for part in parts)
            or parts[-1] in EXCLUDE_FILES
            or any(_has_tail(parts, t) for t in EXCLUDE_PATH_TAILS))


def package_path(out_root, slug: str, version: str) -> Path:
    return Path(out_root) / slug / version


def _iter_src(app_root: Path):
    """有序产出 (rel_posix, Path)：排序保证 hash 稳定，跳过规则集中在这一处。"""
    for p in sorted(app_root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(app_root).parts
        if _is_excluded(parts):
            continue
        yield "/".join(parts), p


def source_hash(app_root: Path) -> str:
    """源码指纹（M3-b 的 fork 判定基线）：排除平台生成物与依赖产物，只认人的改动。"""
    h = hashlib.sha256()
    for rel, p in _iter_src(Path(app_root)):
        h.update(f"{rel}:{anchor_mod.file_md5(p)}\n".encode("utf-8"))
    return h.hexdigest()


def manifest_fingerprint(anchor: list[dict]) -> str:
    return anchor_mod.fingerprint(anchor)


def write_package(*, out_root, slug: str, version: str, app_root: Path,
                  anchor: list[dict], manifest_extra: dict) -> Path:
    """临时目录 → 校验无凭据 → 原子 rename。失败即清临时目录，绝不留半成品正式版。"""
    pkg = package_path(out_root, slug, version)
    if pkg.exists():
        raise DeployError(f"版本包已存在（{slug}@{version}），包不可覆盖", status=409)
    tmp = pkg.parent / f"{version}.tmp{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        (tmp / "src").mkdir(parents=True)
        sql_dir = tmp / "sqlchange"
        sql_dir.mkdir()
        src_sql = Path(app_root) / "sqlchange"
        for e in anchor:                       # 只拷锚点序列内的文件，逐个存在性校验
            f = src_sql / e["filename"]
            if not f.is_file():
                raise DeployError(f"锚点文件缺失: {e['filename']}")
            if anchor_mod.file_md5(f) != e["checksum"]:
                raise DeployError(f"锚点文件与库内记录不一致: {e['filename']}")
            shutil.copy2(f, sql_dir / e["filename"])
        for rel, p in _iter_src(Path(app_root)):
            dst = tmp / "src" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
        manifest = {
            "schema": SCHEMA, "slug": slug, "version": version,
            "anchor": anchor, "fingerprint": manifest_fingerprint(anchor),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "exclude_top": sorted(EXCLUDE_TOP), "exclude_dirs": sorted(EXCLUDE_DIRS),
            "exclude_path_tails": ["/".join(t) for t in EXCLUDE_PATH_TAILS],
            **manifest_extra,
        }
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="")
        # 落盘前的最后一道：包内任何位置都不许出现明文凭据
        for p in tmp.rglob("*"):
            if p.is_file() and p.name in EXCLUDE_FILES:
                raise DeployError(f"包内混入敏感文件: {p.relative_to(tmp)}")
        pkg.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, pkg)                   # 同盘原子改名；目标存在则整体失败（前面已判）
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return pkg


def read_manifest(pkg_dir: Path) -> dict:
    f = Path(pkg_dir) / "manifest.json"
    if not f.is_file():
        raise DeployError(f"版本包缺 manifest.json: {pkg_dir}", status=500)
    return json.loads(f.read_text(encoding="utf-8"))


def package_dir_for(release_row: dict, store_root) -> Path:
    """由 app_release 行定位包目录（install/upgrade 共用，别在调用方各自拼路径）。"""
    return package_path(store_root, release_row["slug"], release_row["version"])
