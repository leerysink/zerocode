# tests/test_store_packages.py
"""包 = 物理快照（不引用活目录）+ 只读 + 同 slug+version 不可覆盖。"""
import json
import pytest
from app.deploy.errors import DeployError
from app.store import packages


@pytest.fixture
def app_tree(tmp_path):
    """造一棵带地雷的应用目录：凭据、依赖、构建产物都不许进包。"""
    root = tmp_path / "apps" / "demo"
    for rel in ("backend/app/main.py", "backend/requirements.txt",
                "frontend/src/App.vue", "frontend/node_modules/dep/index.js",
                "frontend/dist/assets/a.js", "deploy/prod-compose.yml", "deploy/.env",
                "sqlchange/V20260920_01_init.sql", "sqlchange/V20260920_02_more.sql"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n", encoding="utf-8", newline="")
    (root / "app.json").write_text(json.dumps({"slug": "demo", "stack": "vue3-fastapi"}),
                                   encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    return root


def _anchor():
    return [{"filename": "V20260920_01_init.sql", "checksum": None}]


def test_write_package_excludes_secrets_and_artifacts(tmp_path, app_tree):
    a = _anchor()
    a[0]["checksum"] = _md5(app_tree / "sqlchange" / "V20260920_01_init.sql")
    pkg = packages.write_package(
        out_root=tmp_path / "store", slug="demo", version="b1", app_root=app_tree,
        anchor=a, manifest_extra={"title": "演示", "stack": "vue3-fastapi"})
    files = {p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file()}
    assert "manifest.json" in files
    assert "sqlchange/V20260920_01_init.sql" in files
    assert "src/backend/app/main.py" in files
    assert not any(f.endswith(".env") for f in files), "凭据外泄：包里有 .env"
    assert not any("/deploy/" in f or f.startswith("deploy/") for f in files), "包里有 deploy/"
    assert not any("node_modules" in f for f in files)
    assert not any("/dist/" in f for f in files)
    assert "sqlchange/V20260920_02_more.sql" not in files, "未应用的迁移不得进包"


def test_package_is_immutable(tmp_path, app_tree):
    args = dict(out_root=tmp_path / "store", slug="demo", version="b1", app_root=app_tree,
                anchor=_anchor_real(app_tree), manifest_extra={"title": "演示", "stack": "vue3-fastapi"})
    first = packages.write_package(**args)
    (first / "sqlchange" / "V20260920_01_init.sql").write_text("-- 改了\n", encoding="utf-8")
    with pytest.raises(DeployError) as e:
        packages.write_package(**args)
    assert "已存在" in str(e.value)
    # 失败的第二趟不许把第一趟的包目录连带删掉
    assert first.is_dir()


def test_manifest_round_trip(tmp_path, app_tree):
    a = _anchor_real(app_tree)
    pkg = packages.write_package(
        out_root=tmp_path / "store", slug="demo", version="b1", app_root=app_tree,
        anchor=a, manifest_extra={"title": "演示", "stack": "vue3-fastapi"})
    m = packages.read_manifest(pkg)
    assert m["schema"] == "zc-release/1"
    assert m["slug"] == "demo" and m["version"] == "b1"
    assert m["anchor"] == a
    assert m["fingerprint"] == packages.manifest_fingerprint(a)
    assert pkg.is_dir() and not any(p.name.startswith("b1.tmp") for p in pkg.parent.iterdir()), \
        "临时目录必须被 rename 收掉"


def test_source_hash_sensitive_to_src_only(tmp_path, app_tree):
    h1 = packages.source_hash(app_tree)
    (app_tree / "backend" / "app" / "main.py").write_text("# changed\n", encoding="utf-8")
    assert packages.source_hash(app_tree) != h1
    h2 = packages.source_hash(app_tree)
    (app_tree / "deploy" / ".env").write_text("SECRET=x\n", encoding="utf-8")
    assert packages.source_hash(app_tree) == h2, "平台生成物变化不该算作分叉"


def _md5(p):
    from app.store.anchor import file_md5
    return file_md5(p)


def _anchor_real(app_tree):
    return [{"filename": "V20260920_01_init.sql",
             "checksum": _md5(app_tree / "sqlchange" / "V20260920_01_init.sql")}]
