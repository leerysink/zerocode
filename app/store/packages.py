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
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}


def package_path(out_root, slug: str, version: str) -> Path:
    return Path(out_root) / slug / version


def _iter_src(app_root: Path):
    """有序产出 (rel_posix, Path)：排序保证 hash 稳定，跳过规则集中在这一处。"""
    for p in sorted(app_root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(app_root).parts
        if parts[0] in EXCLUDE_TOP:
            continue
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        if parts[-1] in EXCLUDE_FILES:
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
