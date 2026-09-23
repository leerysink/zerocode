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
                "sqlchange/V20260920_01_init.sql", "sqlchange/V20260920_02_more.sql",
                # 大型栈 dev 预览就地跑 mvn 留下的：几百 MB 编译产物 + 一直在滚的日志
                "backend/target/classes/com/zerocode/app/BaseApplication.class",
                "backend/target/demo.jar", "backend/logs/spring.log",
                # 反向地雷：target / logs 都是业务通用词（目标管理、日志页面），不该被一起藏掉
                "frontend/src/views/target/Index.vue", "frontend/src/views/logs/Index.vue",
                # 裁定（B1c 后）：上传件算运行时数据，不进包也不进指纹
                "backend/uploads/2026/09/avatar.png"):
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
    # 两条都锚到 backend/ 下：写成 `"/target/" in f` 会连业务目录一起要（见下两条 in 断言）
    assert not any(f.startswith("src/backend/target/") for f in files), "包里有 maven 编译产物"
    assert not any(f.startswith("src/backend/logs/") for f in files), "包里有运行时日志"
    assert not any(f.startswith("src/backend/uploads/") for f in files), "上传件是运行时数据，不进包"
    assert "src/frontend/src/views/target/Index.vue" in files, "业务目录不该被裸名屏蔽"
    assert "src/frontend/src/views/logs/Index.vue" in files
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


def test_source_hash_ignores_maven_build_churn(tmp_path, app_tree):
    """预览一次就重写一遍 target/：若算进 source_hash，分叉判定会每次预览后误报。"""
    h = packages.source_hash(app_tree)
    t = app_tree / "backend" / "target"
    (t / "new-classes").mkdir(parents=True)
    (t / "new-classes" / "More.class").write_text("# rebuilt\n", encoding="utf-8", newline="")
    (t / "demo.jar").write_text("# rebuilt jar\n", encoding="utf-8", newline="")
    (app_tree / "backend" / "logs" / "spring.log").write_text("# rolled\n", encoding="utf-8", newline="")
    assert packages.source_hash(app_tree) == h
    # 但真源码要算数：同一颗 target 旁的 backend 下改一个文件就必须变
    java = app_tree / "backend" / "src" / "main" / "java"
    java.mkdir(parents=True)
    (java / "App.java").write_text("// new\n", encoding="utf-8", newline="")
    assert packages.source_hash(app_tree) != h


def test_business_target_and_logs_dirs_stay_in_the_package(app_tree):
    """窄判据的对面：只钉死 backend/ 下那两个产物目录，不吃同名的业务目录。

    按裸名屏蔽会把作者的 src/views/target 静默排除出包与 source_hash——作者看到的是
    「包里没有这个页面」且改它不算分叉，比留几份编译产物进包严重得多。
    """
    rels = [rel for rel, _ in packages._iter_src(app_tree)]
    assert "frontend/src/views/target/Index.vue" in rels
    assert "frontend/src/views/logs/Index.vue" in rels
    assert not any(r.startswith("backend/target/") for r in rels)
    assert not any(r.startswith("backend/logs/") for r in rels)


def test_runtime_uploads_are_not_packaged_and_not_fingerprinted(app_tree):
    """用户裁定：backend/uploads/ 算运行时数据，不算应用内容。

    两个后果都要钉：不进包（安装者不被塞作者的示例图）与不进 source_hash（装完之后有人传一张图
    不该让下一次发布报成分叉）。但文件树照旧看得见它们（packages 与 files 两侧名单刻意不同形）。
    """
    rels = [rel for rel, _ in packages._iter_src(app_tree)]
    assert "backend/uploads/2026/09/avatar.png" not in rels
    h = packages.source_hash(app_tree)
    up = app_tree / "backend" / "uploads" / "2026" / "09"
    (up / "more.png").write_text("# new upload\n", encoding="utf-8", newline="")
    (up / "avatar.png").write_text("# replaced\n", encoding="utf-8", newline="")
    assert packages.source_hash(app_tree) == h, "上传不该让分叉判定误报"


def _md5(p):
    from app.store.anchor import file_md5
    return file_md5(p)


def _anchor_real(app_tree):
    return [{"filename": "V20260920_01_init.sql",
             "checksum": _md5(app_tree / "sqlchange" / "V20260920_01_init.sql")}]
