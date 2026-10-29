# tests/test_build_output.py
"""build-output 打包器单测（ADR-0013 / ADR-0014）。

全部用例只在 tmp_path 里搭一个假仓库跑打包，**不连数据库、不碰 docker、不跑 npm**：
打包器的职责是「装配文件 + 守住三条硬约束」，这些都能纯文件层面验证。
边界优先：重放安全、端口撞号、凭据泄漏、陈旧 dist、部署根形态各成一组。
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest
import yaml

# pytest 只把 tests/ 放进 sys.path，仓库根要靠 `python -m pytest` 的 CWD 才在；
# 显式插一次，让本文件在任何调用方式下都能导入 scripts/ 这个命名空间包。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import build_output as bo      # noqa: E402

STAMP = "2026-10-16T00:00:00Z"
ROOT = "/opt/zerocode"

CHANGELOG_INIT = """-- V20260101_01_init.sql
CREATE TABLE IF NOT EXISTS workspace (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    root_path VARCHAR(500) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_project (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(40) NOT NULL UNIQUE,
    scale VARCHAR(10) NOT NULL,
    stack VARCHAR(20) NOT NULL,
    prod_port INT NOT NULL UNIQUE,
    dev_port INT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS app_config (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL
);
INSERT INTO app_config (key, value) VALUES ('llm.model', '"deepseek-chat"')
ON CONFLICT (key) DO NOTHING;
"""

CHANGELOG_VECTOR = """-- V20260102_01_vec.sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS workspace_memory (
    id BIGSERIAL PRIMARY KEY,
    embedding vector
);
ALTER TABLE workspace_memory ALTER COLUMN embedding TYPE vector(1024);
CREATE INDEX IF NOT EXISTS ix_ws_memory_embedding
    ON workspace_memory USING hnsw (embedding vector_cosine_ops);
"""

GATEWAY_COMPOSE = """name: zerocode-gateway
services:
  gateway:
    image: openresty/openresty:alpine
    environment:
      UPSTREAM_HOST: ${ZEROCODE_GATEWAY_UPSTREAM:-host.docker.internal}
      ROUTES_PATH: /etc/nginx/routes/routes.json
      GATEWAY_DOMAIN: ${ZEROCODE_GATEWAY_DOMAIN:-zerocode.com}
    volumes:
      - ./certs:/etc/nginx/certs:ro
      - ./routes:/etc/nginx/routes:ro
"""


def _make_app(apps_root: Path, slug: str, *, name="演示应用", stack="vue3-fastapi",
              prod=8212, dev=8213, with_deploy=True, missing=(), json_slug=None) -> Path:
    """在假仓库里造一个应用工程：源码目录 + app.json + deploy/（含明文口令）+ node_modules。"""
    d = apps_root / slug
    for sub in ("backend", "frontend", "schema", "sqlchange"):
        if sub in missing:
            continue
        (d / sub).mkdir(parents=True, exist_ok=True)
        (d / sub / "placeholder.txt").write_text("x", encoding="utf-8")
    d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "schema": "zc-app/1", "name": name, "slug": json_slug or slug,
        "scale": "small", "stack": stack,
        "ports": {"prod": prod, "dev": dev}}, ensure_ascii=False), encoding="utf-8")
    if with_deploy:
        (d / "deploy").mkdir(parents=True, exist_ok=True)
        (d / "deploy" / ".env").write_text("DEV_PASSWORD=super-secret\n", encoding="utf-8")
        (d / "deploy" / "dev-compose.yml").write_text("name: zc-x-dev\n", encoding="utf-8")
    (d / "node_modules").mkdir(parents=True, exist_ok=True)
    (d / "node_modules" / "junk.js").write_text("// 不该进包\n", encoding="utf-8")
    return d


def _make_gateway(repo: Path) -> Path:
    """在假仓库里造网关配置，含三样**绝不能进包**的东西：私钥、现场路由、本地 .env。"""
    gw = repo / "gateway"
    (gw / "lua").mkdir(parents=True)
    (gw / "docker-compose.yml").write_text(GATEWAY_COMPOSE, encoding="utf-8")
    (gw / "nginx.conf").write_text("env GATEWAY_DOMAIN;\n", encoding="utf-8")
    (gw / "lua" / "router.lua").write_text("return {}\n", encoding="utf-8")
    (gw / "404.html").write_text("<html></html>\n", encoding="utf-8")
    (gw / "make-cert.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (gw / "make-cert.ps1").write_text("# stub\n", encoding="utf-8")
    (gw / "certs").mkdir()
    (gw / "certs" / "gateway.key").write_text("PRIVATE-KEY-MUST-NOT-SHIP", encoding="utf-8")
    (gw / "certs" / "gateway.crt").write_text("CERT-MUST-NOT-SHIP", encoding="utf-8")
    (gw / "routes").mkdir()
    (gw / "routes" / "routes.json").write_text('{"dev-only": 9001}\n', encoding="utf-8")
    (gw / "routes" / ".gitkeep").write_text("", encoding="utf-8")
    (gw / ".env").write_text("ZEROCODE_GATEWAY_DOMAIN=local-only.com\n", encoding="utf-8")
    return gw


@pytest.fixture
def fake_repo(tmp_path) -> Path:
    """最小假仓库：changelog ×2 + 后端源码 + 已构建的前端 dist + 两个预置应用。"""
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "V20260101_01_init.sql").write_text(CHANGELOG_INIT, encoding="utf-8")
    (tmp_path / "changelog" / "V20260102_01_vec.sql").write_text(CHANGELOG_VECTOR, encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("# 平台后端 stub\n", encoding="utf-8")
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "app" / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "App.vue").write_text("<template/>", encoding="utf-8")
    old = time.time() - 3600                       # src 比 dist 旧一小时 → dist 不算陈旧
    os.utime(src / "App.vue", (old, old))

    (tmp_path / "apps").mkdir()
    _make_app(tmp_path / "apps", "demo1")
    _make_app(tmp_path / "apps", "demo2", name="第二个", prod=8220, dev=8221)
    _make_gateway(tmp_path)
    return tmp_path


def _build(repo: Path, **kw) -> dict:
    kw.setdefault("slugs", ("demo1", "demo2"))
    kw.setdefault("deploy_root", ROOT)
    kw.setdefault("generated_at", STAMP)
    kw.setdefault("out", repo / "build-output")
    return bo.build(repo_root=repo, **kw)


# ---------------------------------------------------------------- 装配完整性

def test_build_produces_every_artifact(fake_repo):
    s = _build(fake_repo)
    out = s["out"]
    for rel in ("init.sql", "docker-compose.yml", ".env.example", ".dockerignore",
                "apply-init.sh", "README-deploy.md",
                "backend/Dockerfile", "backend/requirements.txt", "backend/app/main.py",
                "frontend/dist/index.html",
                "gateway/docker-compose.yml", "gateway/nginx.conf", "gateway/lua/router.lua",
                "gateway/404.html", "gateway/make-cert.sh", "gateway/certs/.gitkeep",
                "gateway/routes/routes.json", "gateway/.env.example",
                "apps/demo1/app.json", "apps/demo1/schema/placeholder.txt",
                "apps/demo2/app.json"):
        assert (out / rel).is_file(), f"交付包缺 {rel}"
    assert s["deploy_root"] == ROOT and s["generated_at"] == STAMP
    assert s["preset_files"]["demo1"] > 0 and s["init_sql_bytes"] > 0
    assert s["gateway_files"] > 0


def test_backend_copy_drops_pycache(fake_repo):
    """边界：本机残留的 __pycache__ 不该进交付包（体积与陈旧字节码都是麻烦）。"""
    out = _build(fake_repo)["out"]
    assert not list((out / "backend").rglob("__pycache__"))
    assert not list((out / "backend").rglob("*.pyc"))


def test_rebuild_wipes_previous_output(fake_repo):
    """边界：重跑必须整目录重建——上一次的残留文件混进包里比缺文件更难查。"""
    out = fake_repo / "build-output"
    _build(fake_repo)
    (out / "stale-leftover.txt").write_text("上一次的残留", encoding="utf-8")
    _build(fake_repo)
    assert not (out / "stale-leftover.txt").exists()


def test_generated_text_files_use_lf(fake_repo):
    """边界：CRLF 会让 apply-init.sh 在 bash 下直接报错，SQL 里也不该有 \\r。"""
    out = _build(fake_repo)["out"]
    for rel in ("init.sql", "docker-compose.yml", "apply-init.sh", "README-deploy.md",
                ".env.example", "backend/Dockerfile"):
        raw = (out / rel).read_bytes()
        assert b"\r\n" not in raw, f"{rel} 含 CRLF 行尾"


# ---------------------------------------------------------------- init.sql 合成

def test_init_sql_keeps_changelog_order(fake_repo):
    """结构段按文件名字典序（= 时间序）拼接，顺序错了迁移会因缺表而失败。"""
    sql = (_build(fake_repo)["out"] / "init.sql").read_text(encoding="utf-8")
    assert sql.index("V20260101_01_init.sql") < sql.index("V20260102_01_vec.sql")
    assert "CREATE TABLE IF NOT EXISTS workspace" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql


def test_init_sql_guards_unguarded_vector_alter(fake_repo):
    """边界：无守卫的 ALTER COLUMN TYPE vector(N) 必须被换成 DO 块——重放它等于全表重写 + 持锁。"""
    sql = (_build(fake_repo)["out"] / "init.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE workspace_memory ALTER COLUMN embedding TYPE vector(1024);" not in sql
    assert "$zcvec$" in sql and "to_regclass('workspace_memory')" in sql
    assert "IS DISTINCT FROM 'vector(1024)'" in sql
    # changelog 原文不许被改写（后端启动迁移靠它，且 split_statements 不认 $$）
    assert "ALTER TABLE workspace_memory ALTER COLUMN embedding TYPE vector(1024);" in (
        fake_repo / "changelog" / "V20260102_01_vec.sql").read_text(encoding="utf-8")


def test_init_sql_seed_rewrites_root_path_and_leaves_owner_null(fake_repo):
    """预置应用种子行：root_path 用部署根、owner_user_id 留 NULL 交给创始人事务承接。"""
    sql = (_build(fake_repo)["out"] / "init.sql").read_text(encoding="utf-8")
    assert f"{ROOT}/apps/demo1" in sql and f"{ROOT}/apps/demo2" in sql
    assert "8212" in sql and "8221" in sql
    assert "owner_user_id" not in sql.split("预置应用种子")[1]     # 种子段不许显式写归属列
    assert "$zcpreset$" in sql and "IF NOT EXISTS (SELECT 1 FROM app_project WHERE slug = 'demo1')" in sql
    assert str(fake_repo) not in sql                              # 本机 Windows 路径一个都不许带过去


def test_init_sql_without_presets_has_no_seed_section(fake_repo):
    """边界：只打平台（slugs 为空）时不产出种子段，但其余交付物照常。"""
    s = _build(fake_repo, slugs=())
    sql = (s["out"] / "init.sql").read_text(encoding="utf-8")
    assert "$zcpreset$" not in sql and "预置应用种子" not in sql
    assert "CREATE TABLE IF NOT EXISTS workspace" in sql
    assert (s["out"] / "docker-compose.yml").is_file()
    assert s["presets"] == [] and "（本次未预置应用）" in (
        s["out"] / "README-deploy.md").read_text(encoding="utf-8")


def test_init_sql_is_byte_stable_for_same_inputs(fake_repo):
    """边界：同样输入两次合成必须逐字节相同——否则每次发布的 diff 都是噪音，没法审阅。"""
    a = _build(fake_repo, out=fake_repo / "bo-a")["out"] / "init.sql"
    b = _build(fake_repo, out=fake_repo / "bo-b")["out"] / "init.sql"
    assert a.read_bytes() == b.read_bytes()


def test_preset_name_with_quote_is_escaped(fake_repo):
    """边界：应用名含单引号时种子 SQL 必须转义，否则整段语法错、部署当场失败。"""
    _make_app(fake_repo / "apps", "demoq", name="张三's 应用", prod=8230, dev=8231)
    sql = _build(fake_repo, slugs=("demoq",))["out"].joinpath("init.sql").read_text(encoding="utf-8")
    assert "'张三''s 应用'" in sql


def test_empty_changelog_refuses_to_emit_init_sql(tmp_path):
    """边界：changelog 一个文件都没有时不许静默产出空 init.sql（那会部署出一个没表的库）。"""
    (tmp_path / "changelog").mkdir()
    with pytest.raises(bo.BuildError, match="没有任何 V\\*\\.sql"):
        bo.synth_init_sql(tmp_path / "changelog", [], ROOT, generated_at=STAMP)


@pytest.mark.parametrize("bad,why", [
    ("INSERT INTO app_config (key, value) VALUES ('x', '1');", "缺 ON CONFLICT"),
    ("INSERT INTO app_config (key, value) VALUES ('x', '1') ON CONFLICT (key) DO UPDATE SET value = '2';",
     "DO UPDATE"),
    ("DELETE FROM app_config WHERE key = 'x';", "DELETE FROM"),
    ("TRUNCATE workspace;", "TRUNCATE"),
    ("DROP TABLE workspace;", "DROP TABLE"),
    # 裸 UPDATE = 「每次发布都把服务器值拍回本地值」，与只补不覆盖直接相左
    ("UPDATE app_config SET value = '9' WHERE key = 'deploy.keep_versions';", "裸 UPDATE"),
    ("UPDATE actor SET is_founder = false;", "裸 UPDATE"),
    # 下面四条都会在存量库上重放报错，DROP COLUMN 还会先删一次真数据
    ("ALTER TABLE app_config DROP COLUMN value;", "DROP COLUMN"),
    ("DROP INDEX ix_app_config_key;", "DROP INDEX"),
    ("INSERT INTO t (a) WITH q AS (SELECT 1) SELECT * FROM q;", "缺 ON CONFLICT"),
    ("INSERT INTO t (a) VALUES (1) ON CONFLICT ON CONSTRAINT t_a_key DO NOTHING;",
     "ON CONSTRAINT"),
])
def test_replay_unsafe_changelog_refuses_package(tmp_path, bad, why):
    """边界：「每次发布重放全量」的前提是包里没有会洗数据/插重复行的语句，违者拒绝出包。"""
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "V20260101_01_bad.sql").write_text(
        CHANGELOG_INIT + "\n" + bad + "\n", encoding="utf-8")
    with pytest.raises(bo.BuildError) as ei:
        bo.synth_init_sql(tmp_path / "changelog", [], ROOT, generated_at=STAMP)
    assert "重放不安全" in str(ei.value) and why in str(ei.value)


def test_audit_allows_on_delete_cascade(tmp_path):
    """边界：外键的 ON DELETE CASCADE 不是 DELETE FROM，不该被审计误杀（否则真 changelog 打不出包）。"""
    assert bo.audit_replay_safety(
        "CREATE TABLE t (a BIGINT REFERENCES w(id) ON DELETE CASCADE);", "x") == []


def test_audit_does_not_mistake_updated_at_column_for_bare_update():
    """边界：列名里的 `updated_at` 含子串 UPDATE，用子串匹配会把所有建表语句误杀。

    必须按语句前缀判：否则真 changelog 里六个带 updated_at 的表一个也打不出包。
    """
    sql = ("CREATE TABLE IF NOT EXISTS t (\n"
           "    id BIGSERIAL PRIMARY KEY,\n"
           "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()\n);\n"
           "INSERT INTO t (id) VALUES (1) ON CONFLICT DO NOTHING;\n")
    assert bo.audit_replay_safety(sql, "x") == []


def test_bare_update_only_in_comment_is_fine():
    """边界：注释里引用 UPDATE 是给人看的说明，不得被当成可执行语句拦下。"""
    sql = ("-- 旧的写法是 UPDATE app_config SET value = '60' WHERE key = 'x';\n"
           "CREATE TABLE IF NOT EXISTS t (id BIGSERIAL PRIMARY KEY);\n")
    assert bo.audit_replay_safety(sql, "x") == []


# ------------------------------------------------- 裸 UPDATE 的显式处置表
#
# changelog 里现存两条裸 UPDATE，原文不能动（增量轨靠它），只能在合成期处置：
#   V20260920_01 的默认值抬升→ 改写成「只在仍是旧默认时才抬」（否则运维调大过的
#     deploy.health_timeout 会在下次发布被静默拍回 240，大型栈预览随即超时）；
#   V20260925_01 的昵称去重 → 它自己的 WHERE 就是自限的，保留原句。
# 表外的一律拒包（fail-closed）。

HEALTH_TIMEOUT_BUMP = "UPDATE app_config SET value = '240' WHERE key = 'deploy.health_timeout';"
ACTOR_DEDUP = ("UPDATE actor\n"
               "   SET nickname = nickname || '#' || id\n"
               " WHERE id NOT IN (SELECT MIN(id) FROM actor GROUP BY nickname);")


def test_health_timeout_bump_is_rewritten_to_value_guarded():
    """回归：无条件抬升默认值的 UPDATE 必须改成「仅当仍是旧默认」，否则重放会洗掉运维的改动。"""
    out, hits = bo.rewrite_unsafe_updates(HEALTH_TIMEOUT_BUMP + "\n")
    assert hits == 1
    active = " ".join(bo._strip_sql_comments(out).split())
    assert "AND value::text = '60'" in active, "未加上值守卫，重放仍会覆盖"
    assert bo.audit_replay_safety(out, "x") == [], "改写后应能通过审计"


def test_actor_dedup_update_is_kept_verbatim_as_self_limiting():
    """边界：已登记的自限语句不得被改写（改它反而可能弄坏去重语义），且不得被审计拦下。"""
    out, hits = bo.rewrite_unsafe_updates(ACTOR_DEDUP + "\n")
    assert hits == 0 and out == ACTOR_DEDUP + "\n"
    assert bo.audit_replay_safety(ACTOR_DEDUP, "x") == []


def test_dispositioned_update_drift_fails_closed(tmp_path):
    """边界：已登记的语句一旦改了形（值从 240 改成 300），必须重新过人工评审而不是静默放行。

    这是处置表的自我校验：否则将来谁改了 changelog，守卫会在没人知道的情况下失效。
    """
    drifted = HEALTH_TIMEOUT_BUMP.replace("'240'", "'300'")
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "V20260101_01_drift.sql").write_text(
        CHANGELOG_INIT + "\n" + drifted + "\n", encoding="utf-8")
    with pytest.raises(bo.BuildError, match="裸 UPDATE"):
        bo.synth_init_sql(tmp_path / "changelog", [], ROOT, generated_at=STAMP)


def test_dispositioned_update_missing_trailing_semicolon_fails_closed(tmp_path):
    """边界：登记过的语句只是漏了结尾分号，也必须拒包——这里曾经是 fail-open。

    漏分号在增量轨上完全合法（app/db.py 的切分器把文件尾残句照跑），没有任何地方会提示。
    而合成期三处判定用的是两套域：审计走 _normalize_statement（会 rstrip 分号）→ 认定
    「已登记」放行；改写走 _loose_re（末尾锚在分号前，那是改写幂等的保证，不能动）→ 匹配
    不到，静默不改；兜底若复用同一个正则 → 同样匹配不到，不报警。叠加起来就是无守卫的原句
    直接进产物，footer 还写「改写 0 处」，看上去完全合法。修法只能是让兜底与审计同源。
    """
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "V20260101_01_init.sql").write_text(CHANGELOG_INIT, encoding="utf-8")
    (tmp_path / "changelog" / "V20260920_01_bump.sql").write_text(
        HEALTH_TIMEOUT_BUMP.rstrip(";") + "\n", encoding="utf-8")
    with pytest.raises(bo.BuildError, match="裸 UPDATE"):
        bo.synth_init_sql(tmp_path / "changelog", [], ROOT, generated_at=STAMP)


def test_init_sql_end_to_end_carries_both_real_updates_safely(tmp_path):
    """端到端：两条真实历史语句同时在场时，产物里既不得有无条件抬升，也得保留去重句。"""
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "V20260101_01_init.sql").write_text(CHANGELOG_INIT, encoding="utf-8")
    (tmp_path / "changelog" / "V20260102_01_bump.sql").write_text(
        HEALTH_TIMEOUT_BUMP + "\n", encoding="utf-8")
    (tmp_path / "changelog" / "V20260103_01_dedup.sql").write_text(
        ACTOR_DEDUP + "\n", encoding="utf-8")

    sql = bo.synth_init_sql(tmp_path / "changelog", [], ROOT, generated_at=STAMP)
    active = " ".join(bo._strip_sql_comments(sql).split())
    # 比完整语句（含分号）而不是去分号的前缀：改写后的句子正是以原句为前缀再追加守卫，
    # 拿前缀做子串判定会把「已正确改写」误判成「原封不动」。
    assert HEALTH_TIMEOUT_BUMP not in active, "无条件抬升句原封不动地进了产物"
    assert "AND value::text = '60'" in active
    assert "nickname || '#' || id" in active          # 去重句保留
    assert "裸 UPDATE 改写 1 处" in sql               # footer 如实记账


# ------------------------------------------------- ON CONFLICT 仲裁目标剔除（重放回归）

# 真实库里 V20260925_02 就是这么把 app_config 的主键换成联合唯一索引的。
CHANGELOG_OWNER_COLS = """-- V20260103_01_owner.sql
ALTER TABLE app_config ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
-- 写侧 ON CONFLICT (owner_user_id, key) 以此索引为仲裁目标。
CREATE UNIQUE INDEX IF NOT EXISTS ux_app_config_owner_key
    ON app_config (owner_user_id, key) NULLS NOT DISTINCT;
ALTER TABLE app_config DROP CONSTRAINT IF EXISTS app_config_pkey;
"""


def test_init_sql_strips_conflict_arbitration_target(fake_repo):
    """回归：后续迁移把 app_config 主键换成联合唯一索引后，`ON CONFLICT (key)` 在第二遍
    重放时必然报「no unique or exclusion constraint matching」——已在真 pgvector 上踩到。"""
    (fake_repo / "changelog" / "V20260103_01_owner.sql").write_text(
        CHANGELOG_OWNER_COLS, encoding="utf-8")
    sql = _build(fake_repo)["out"].joinpath("init.sql").read_text(encoding="utf-8")
    active = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    assert [ln for ln in active if "ON CONFLICT (" in ln] == []     # 仲裁目标一个不留
    assert "ON CONFLICT DO NOTHING" in "\n".join(active)
    assert "仲裁目标剔除 1 处" in sql                              # 改写要在产物里留下审计痕迹
    # changelog 原文不动：后端启动迁移按原文执行，且它每个文件只跑一次，不受这个坑影响
    assert "ON CONFLICT (key) DO NOTHING" in (
        fake_repo / "changelog" / "V20260101_01_init.sql").read_text(encoding="utf-8")


def test_strip_conflict_targets_leaves_comments_alone():
    """边界：注释里的 ON CONFLICT (列…) 是给人看的说明，不得被改写成误导性文本。"""
    src = ("-- 写侧 ON CONFLICT (owner_user_id, key) 以此为仲裁目标\n"
           "INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO NOTHING;\n")
    out, hits = bo.strip_conflict_targets(src)
    assert hits == 1
    assert "-- 写侧 ON CONFLICT (owner_user_id, key) 以此为仲裁目标" in out
    assert "ON CONFLICT DO NOTHING;" in out


def test_strip_conflict_targets_noop_when_already_targetless():
    """边界：本就无目标的形态不得计数也不得改文——否则每次发布的 diff 都是噪音。"""
    src = "INSERT INTO t (a) VALUES (1)\nON CONFLICT DO NOTHING;\n"
    assert bo.strip_conflict_targets(src) == (src, 0)


def test_cross_line_conflict_target_fails_closed(tmp_path):
    """边界：跨行书写的 ON CONFLICT 逐行剔不掉，必须打包期拒绝，而不是产出会重放报错的脚本。"""
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "V20260101_01_x.sql").write_text(
        CHANGELOG_INIT.replace("ON CONFLICT (key) DO NOTHING;",
                               "ON CONFLICT (key)\n    DO NOTHING;"), encoding="utf-8")
    with pytest.raises(bo.BuildError, match="同一行"):
        bo.synth_init_sql(tmp_path / "changelog", [], ROOT, generated_at=STAMP)


# ------------------------------------------------- 幂等守卫补齐（重放回归）

# 真实库里 V20260917_02 就是这么写的：CREATE TABLE / CREATE INDEX / 多动作 ADD COLUMN 全无守卫。
CHANGELOG_UNGUARDED = """-- V20260104_01_unguarded.sql
CREATE TABLE model_config (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL
);
CREATE UNIQUE INDEX uk_model_default ON model_config (name);
ALTER TABLE app_project ADD COLUMN model_config_id BIGINT;
ALTER TABLE app_project ADD COLUMN ctx TEXT, ADD COLUMN reason TEXT;
"""


def test_init_sql_adds_missing_if_not_exists(fake_repo):
    """回归：无守卫的 CREATE TABLE/INDEX/ADD COLUMN 在第二遍重放时必报 already exists
    ——已在真 pgvector 上踩到（model_config）。"""
    (fake_repo / "changelog" / "V20260104_01_unguarded.sql").write_text(
        CHANGELOG_UNGUARDED, encoding="utf-8")
    sql = _build(fake_repo)["out"].joinpath("init.sql").read_text(encoding="utf-8")
    active = "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))
    assert "CREATE TABLE IF NOT EXISTS model_config" in active
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uk_model_default" in active
    # 多动作 ALTER：两个 ADD COLUMN 各自都要带守卫，只补第一个会在重放时炸第二个
    assert "ADD COLUMN IF NOT EXISTS ctx TEXT, ADD COLUMN IF NOT EXISTS reason TEXT" in active
    assert "幂等守卫补齐 5 处" in sql                    # 改写要在产物里留下审计痕迹
    # changelog 原文不动：已应用的迁移就是历史，改它等于重写历史
    assert "CREATE TABLE model_config (" in (
        fake_repo / "changelog" / "V20260104_01_unguarded.sql").read_text(encoding="utf-8")


def test_add_idempotence_guards_noop_when_already_guarded():
    """边界：已带守卫的语句不得被二次插入（否则产出 IF NOT EXISTS IF NOT EXISTS 直接语法错）。"""
    src = ("CREATE TABLE IF NOT EXISTS t (a INT);\n"
           "CREATE INDEX IF NOT EXISTS ix ON t (a);\n"
           "CREATE EXTENSION IF NOT EXISTS vector;\n"
           "ALTER TABLE t ADD COLUMN IF NOT EXISTS b INT;\n")
    assert bo.add_idempotence_guards(src) == (src, 0)


def test_add_idempotence_guards_leaves_comments_alone():
    """边界：注释里提到 CREATE TABLE 时不得被改写，否则历史说明会变成假描述。"""
    src = "-- 这里 CREATE TABLE t 是历史说明\nCREATE TABLE t (a INT);\n"
    out, hits = bo.add_idempotence_guards(src)
    assert hits == 1
    assert "-- 这里 CREATE TABLE t 是历史说明" in out
    assert "CREATE TABLE IF NOT EXISTS t (a INT);" in out


@pytest.mark.parametrize("stmt,why", [
    ("ALTER TABLE app_config ADD CONSTRAINT ck_x CHECK (value IS NOT NULL);", "ADD CONSTRAINT"),
    ("CREATE TYPE mood AS ENUM ('sad', 'ok');", "CREATE TYPE"),
    ("ALTER TABLE workspace RENAME COLUMN name TO title;", "RENAME"),
    ("CREATE VIEW v AS SELECT 1;", "OR REPLACE"),
    ("ALTER TABLE workspace_memory ALTER COLUMN embedding TYPE text;", "全表重写"),
])
def test_non_guardable_ddl_refuses_package(tmp_path, stmt, why):
    """边界：PG 没有守卫形态的 DDL 必须打包期拒绝，而不是产出会在服务器上炸的脚本。"""
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "V20260101_01_x.sql").write_text(
        CHANGELOG_INIT + "\n" + stmt + "\n", encoding="utf-8")
    with pytest.raises(bo.BuildError) as ei:
        bo.synth_init_sql(tmp_path / "changelog", [], ROOT, generated_at=STAMP)
    assert "重放不安全" in str(ei.value) and why in str(ei.value)


def test_audit_idempotence_passes_guarded_ddl():
    """已守卫的常见 DDL 不得被误报（否则真 changelog 永远打不出包）。"""
    ok = ("CREATE TABLE IF NOT EXISTS t (a INT);\n"
          "CREATE UNIQUE INDEX IF NOT EXISTS ix ON t (a);\n"
          "ALTER TABLE t ADD COLUMN IF NOT EXISTS b INT, ADD COLUMN IF NOT EXISTS c INT;\n"
          "ALTER TABLE t DROP CONSTRAINT IF EXISTS t_pkey;\n"
          "DROP INDEX IF EXISTS ix;\n")
    assert bo.audit_idempotence(ok, "x") == []


def test_audit_idempotence_flags_second_unguarded_action_only(tmp_path):
    """边界：一句 ALTER 里第一个 ADD COLUMN 已守卫、第二个未守卫，仍必须被拦下。"""
    problems = bo.audit_idempotence(
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS b INT, ADD COLUMN c INT;", "x")
    assert problems and "ADD COLUMN" in problems[0]


# ---------------------------------------------------------------- 端口与清单校验

def test_port_conflict_between_presets_refuses(fake_repo):
    """边界：prod_port 是 UNIQUE 列，两个预置应用撞号必须打包期拦下（存量 app.json 真有重复端口）。"""
    _make_app(fake_repo / "apps", "demo3", prod=8212, dev=8240)     # 与 demo1 的 prod 撞
    with pytest.raises(bo.BuildError) as ei:
        _build(fake_repo, slugs=("demo1", "demo3"))
    assert "端口冲突" in str(ei.value) and "prod_port=8212" in str(ei.value)
    assert "demo1" in str(ei.value) and "demo3" in str(ei.value)


def test_dev_port_conflict_refuses(fake_repo):
    _make_app(fake_repo / "apps", "demo4", prod=8250, dev=8213)     # 与 demo1 的 dev 撞
    with pytest.raises(bo.BuildError, match="dev_port=8213"):
        _build(fake_repo, slugs=("demo1", "demo4"))


def test_same_prod_dev_port_on_one_app_refuses(fake_repo):
    """边界：同一应用 prod 与 dev 同端口，起预览就会和正式环境抢端口。"""
    _make_app(fake_repo / "apps", "demo5", prod=8260, dev=8260)
    with pytest.raises(bo.BuildError, match="prod 与 dev 端口相同"):
        _build(fake_repo, slugs=("demo5",))


def test_unknown_slug_refuses(fake_repo):
    with pytest.raises(bo.BuildError, match="目录不存在"):
        _build(fake_repo, slugs=("nope",))


def test_missing_app_json_refuses(fake_repo):
    (fake_repo / "apps" / "demo1" / "app.json").unlink()
    with pytest.raises(bo.BuildError, match="缺 app\\.json"):
        _build(fake_repo, slugs=("demo1",))


def test_missing_schema_dir_refuses(fake_repo):
    """边界：缺 schema/ 的应用到了服务器上点预览必挂，打包期就该拒绝。"""
    shutil.rmtree(fake_repo / "apps" / "demo1" / "schema")
    with pytest.raises(bo.BuildError, match="缺 schema/ 目录"):
        _build(fake_repo, slugs=("demo1",))


def test_slug_mismatch_refuses(fake_repo):
    """边界：目录名与 app.json 的 slug 不一致时，种子行与源码目录会对不上号。"""
    _make_app(fake_repo / "apps", "demox", json_slug="other")
    with pytest.raises(bo.BuildError, match="不一致"):
        _build(fake_repo, slugs=("demox",))


def test_broken_app_json_refuses(fake_repo):
    (fake_repo / "apps" / "demo1" / "app.json").write_text("{ 不是 JSON", encoding="utf-8")
    with pytest.raises(bo.BuildError, match="不是合法 JSON"):
        _build(fake_repo, slugs=("demo1",))


def test_bad_ports_refuses(fake_repo):
    meta = json.loads((fake_repo / "apps" / "demo1" / "app.json").read_text(encoding="utf-8"))
    meta["ports"]["prod"] = "8212"                                  # 字符串不是正整数
    (fake_repo / "apps" / "demo1" / "app.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(bo.BuildError, match="ports\\.prod"):
        _build(fake_repo, slugs=("demo1",))


# ---------------------------------------------------------------- 凭据不出包

def test_preset_copy_excludes_deploy_and_node_modules(fake_repo):
    out = _build(fake_repo)["out"]
    assert not (out / "apps" / "demo1" / "deploy").exists()
    assert not (out / "apps" / "demo1" / "node_modules").exists()
    assert (out / "apps" / "demo1" / "sqlchange").is_dir()          # 该带的还得带


def test_no_env_file_anywhere_in_package(fake_repo):
    """整个交付包里不许出现 .env（.env.example 是占位模板，另论）。"""
    out = _build(fake_repo)["out"]
    assert [p.name for p in out.rglob(".env")] == []
    assert "super-secret" not in "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in out.rglob("*") if p.is_file() and p.suffix in {".sql", ".yml", ".md", ".sh", ""})


def test_leaked_env_in_preset_aborts_packaging(fake_repo, monkeypatch):
    """边界：万一 ignore 名单漏了导致 .env 被复制进去，出包终检必须拦下而不是照发。"""
    real_copytree = bo.shutil.copytree

    def copy_with_deploy_env(src, dst, *args, **kw):
        real_copytree(src, dst, *args, **kw)
        # copytree 递归时会回调被替换的自己，故只在应用根注入泄漏，否则每个子目录都长出 deploy/
        if Path(dst).name != "demo1":
            return
        leak = Path(dst) / "deploy"
        leak.mkdir(exist_ok=True)
        (leak / ".env").write_text("DEV_PASSWORD=leak\n", encoding="utf-8")

    monkeypatch.setattr(bo.shutil, "copytree", copy_with_deploy_env)
    with pytest.raises(bo.BuildError, match="deploy/"):
        _build(fake_repo, slugs=("demo1",))


# ---------------------------------------------------------------- 部署根与前端 dist

@pytest.mark.parametrize("raw,expected", [
    ("/opt/zerocode", "/opt/zerocode"),
    ("/opt/zerocode/", "/opt/zerocode"),
    ("  /opt/zc  ", "/opt/zc"),
    ("/opt/zerocode//apps", "/opt/zerocode/apps"),
])
def test_deploy_root_normalization(raw, expected):
    assert bo.normalize_deploy_root(raw) == expected


@pytest.mark.parametrize("raw,why", [
    ("opt/zerocode", "相对路径"),
    ("D:\\project\\zerocode", "Windows 路径"),
    ("", "空串"),
    ("/", "文件系统根"),
    ("///", "只有斜杠"),
])
def test_deploy_root_rejects_bad_forms(raw, why):
    """边界：部署根同时是宿主与容器内路径，形态错了预置应用会集体失联，必须当场拒。"""
    with pytest.raises(bo.BuildError):
        bo.normalize_deploy_root(raw)


def test_deploy_root_flows_into_every_artifact(fake_repo):
    out = _build(fake_repo, deploy_root="/srv/zc")["out"]
    assert "/srv/zc/apps/demo1" in (out / "init.sql").read_text(encoding="utf-8")
    assert "/srv/zc" in (out / ".env.example").read_text(encoding="utf-8")
    compose = (out / "docker-compose.yml").read_text(encoding="utf-8")
    assert "/srv/zc/apps:/srv/zc/apps" in compose
    assert "/srv/zc" in (out / "README-deploy.md").read_text(encoding="utf-8")


def test_backend_dockerfile_workdir_equals_deploy_root(fake_repo):
    """回归：WORKDIR 必须等于部署根，否则 REPO_ROOT 与同路径挂载点对不上。

    settings.py 的 REPO_ROOT 由自身文件位置推出（镜像里就是 WORKDIR），deploy.apps_root
    留空时的兜底是 REPO_ROOT/apps。WORKDIR 写死 /opt/zerocode 而部署根是 /srv/zc 的话，
    新建应用会落进容器可写层，宿主 docker daemon 找不到那个路径 → 起预览必挂。
    """
    out = _build(fake_repo, deploy_root="/srv/zc")["out"]
    dockerfile = (out / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "WORKDIR /srv/zc\n" in dockerfile
    assert "/opt/zerocode" not in dockerfile


def test_deploy_root_is_pinned_not_overridable_at_runtime(fake_repo):
    """边界：部署根一旦打包就钉死，不得留 ${DEPLOY_ROOT:-…} 这种运行期覆盖口子。

    WORKDIR 是构建期烘进镜像的，运行期改 .env 只会让挂载点与 REPO_ROOT 分家：
    新建应用落进容器可写层、宿主 daemon 找不到路径，且全程没有任何报错。
    宁可让「改 .env 换部署根」明确无效，也不留一类静默故障。
    """
    out = _build(fake_repo, deploy_root="/srv/zc")["out"]
    doc = yaml.safe_load((out / "docker-compose.yml").read_text(encoding="utf-8"))
    backend = doc["services"]["backend"]
    # 按 YAML 结构断言而不是文本包含：compose 的注释里会解释「为什么刻意不写成
    # ${DEPLOY_ROOT:-…}」，文本级断言会被这段说明自己误伤。
    assert all("${" not in v for v in backend["volumes"]), "挂载路径留了运行期覆盖口子"
    assert backend["volumes"][1:] == ["/srv/zc/apps:/srv/zc/apps",
                                     "/srv/zc/data:/srv/zc/data",
                                     "/srv/zc/store:/srv/zc/store",
                                     "/srv/zc/gateway/routes:/srv/zc/gateway/routes"]
    # ZEROCODE_DATA_DIR 同样不得取 .env（而 DATABASE_URL 里的口令占位符是合法的，另论）
    assert backend["environment"]["ZEROCODE_DATA_DIR"] == "/srv/zc/data"
    env = (out / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"^DEPLOY_ROOT=", env, re.MULTILINE), \
        ".env 里留一个改了就会静默坏掉的 DEPLOY_ROOT，等于留陷阱"
    assert "/srv/zc" in env                            # 但要写清这个包是按哪个根打的


def test_missing_dist_refuses(fake_repo):
    shutil.rmtree(fake_repo / "frontend" / "dist")
    with pytest.raises(bo.BuildError, match="前端未构建"):
        _build(fake_repo)


def test_stale_dist_refuses(fake_repo):
    """边界：dist 比 src 旧 = 打进去的是旧界面（「功能莫名缺失」的头号原因），默认拒绝。"""
    src_file = fake_repo / "frontend" / "src" / "App.vue"
    future = time.time() + 3600
    os.utime(src_file, (future, future))
    with pytest.raises(bo.BuildError) as ei:
        _build(fake_repo)
    assert "dist 比 src 旧" in str(ei.value) and "--allow-stale-dist" in str(ei.value)


def test_stale_dist_allowed_with_explicit_flag(fake_repo):
    src_file = fake_repo / "frontend" / "src" / "App.vue"
    future = time.time() + 3600
    os.utime(src_file, (future, future))
    assert _build(fake_repo, allow_stale_dist=True)["out"].joinpath(
        "frontend/dist/index.html").is_file()


# ---------------------------------------------------------------- compose 与交付文档

def test_compose_pins_pg_name_vector_image_and_docker_sock(fake_repo):
    """PG 容器名被平台代码写死、镜像必须带 pgvector、后端必须能摸到宿主 docker.sock。"""
    compose = (_build(fake_repo)["out"] / "docker-compose.yml").read_text(encoding="utf-8")
    assert "container_name: zerocode-pg16" in compose
    assert "image: pgvector/pgvector:pg16" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose
    assert "docker-compose-plugin" in (_build(fake_repo)["out"] / "backend" / "Dockerfile"
                                       ).read_text(encoding="utf-8")


def test_compose_creates_zcnet_by_name(fake_repo):
    """应用栈以 external: true 引用 zcnet，得有人先按这个确切名字建出来——平台 compose 就是那个人。

    按 YAML 结构断言而非文本包含：compose 里的注释会提到 external，文本级断言会被自己误伤。
    """
    compose = (_build(fake_repo)["out"] / "docker-compose.yml").read_text(encoding="utf-8")
    doc = yaml.safe_load(compose)                    # 顺带验明生成的 compose 是合法 YAML
    zcnet = doc["networks"]["zcnet"]
    assert zcnet["name"] == "zcnet"
    assert "external" not in zcnet                    # 自己建出来，不能声明成「外部已存在」


def test_platform_backend_can_reach_host_published_app_ports(fake_repo):
    """平台容器必须探得到宿主上发布的应用预览端口，否则演示机上「启动预览」必然假失败。

    容器里的 127.0.0.1:<dev 端口> 上没有任何监听（那是宿主侧的发布端口），健康探针会连着
    整个超时窗口全轮连接拒绝，把已经就绪的栈报成 error。修法与网关同形：extra_hosts 指到
    host-gateway，探测与展示地址都留成服务器 `.env` 可覆盖的运行期位。
    """
    out = _build(fake_repo)["out"]
    backend = yaml.safe_load((out / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["backend"]
    assert backend["extra_hosts"] == ["host.docker.internal:host-gateway"]
    env = backend["environment"]
    assert env["ZEROCODE_PREVIEW_PROBE_HOST"] == "${ZEROCODE_PREVIEW_PROBE_HOST:-host.docker.internal}"
    assert env["ZEROCODE_PREVIEW_HOST"] == "${ZEROCODE_PREVIEW_HOST:-localhost}"
    example = (out / ".env.example").read_text(encoding="utf-8")
    assert "ZEROCODE_PREVIEW_PROBE_HOST=" in example
    assert "ZEROCODE_PREVIEW_HOST=" in example
    readme = (out / "README-deploy.md").read_text(encoding="utf-8")
    # 三个文档都得认这一对，缺一个就必然漂移：排障时读 README 的人要能找到那个变量名
    assert "ZEROCODE_PREVIEW_PROBE_HOST" in readme and "ZEROCODE_PREVIEW_HOST" in readme


def test_ssl_switch_exposed_for_server_env(fake_repo):
    """SSL 开关要能在服务器 .env 里改得到：compose 给运行期覆盖位，.env.example 给裸值示例。

    关掉它的效果由 tests/test_nics.py 钉（地址投影成 IP 直连）；这里只钉交付物的接线面——
    变量名三段一致（nics 读取 / compose 插值 / .env 示例），README 里也能搜到。
    """
    out = _build(fake_repo)["out"]
    backend = yaml.safe_load((out / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["backend"]
    assert backend["environment"]["ZEROCODE_SSL_ENABLE"] == "${ZEROCODE_SSL_ENABLE:-true}"
    example = (out / ".env.example").read_text(encoding="utf-8")
    assert "\nZEROCODE_SSL_ENABLE=true" in example
    assert "ZEROCODE_SSL_ENABLE" in (out / "README-deploy.md").read_text(encoding="utf-8")


def test_dockerignore_keeps_apps_out_of_build_context(fake_repo):
    """边界：apps/ 若进了构建上下文，每次 build 都要把两个应用源码传给 daemon。"""
    ignore = (_build(fake_repo)["out"] / ".dockerignore").read_text(encoding="utf-8")
    for line in ("apps", "data", "store", "init.sql", ".env"):
        assert line in ignore.splitlines()


def test_env_example_carries_placeholder_not_secret(fake_repo):
    env = (_build(fake_repo)["out"] / ".env.example").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=change-me-please" in env
    assert "POSTGRES_USER=zerocode" in env


def test_readme_documents_replay_semantics_and_founder(fake_repo):
    """README 必须把两条最容易踩的语义写清：重放只补不覆盖、首注册者承接预置应用。"""
    readme = (_build(fake_repo)["out"] / "README-deploy.md").read_text(encoding="utf-8")
    assert "只补不覆盖" in readme and "创始人" in readme
    assert "apply-init.sh" in readme and "docker compose up -d --build" in readme
    assert "demo1" in readme and "demo2" in readme


def test_readme_does_not_claim_migrations_run_twice(fake_repo):
    """回归：README 不得再声称「backend 首启会把 changelog 重放一遍」。

    镜像只 COPY 了 backend/app 与 frontend/dist，根本没有 changelog/；run_migrations 面对
    不存在的目录会拿空迭代器静默空转。文档这么写会让人以为忘跑 apply-init.sh 也没事，
    而事实是：忘了它新表不会出现，且没有任何报错提示你。
    """
    readme = (_build(fake_repo)["out"] / "README-deploy.md").read_text(encoding="utf-8")
    assert "跑两遍" not in readme and "重放一遍" not in readme
    assert "唯一 schema 通道" in readme
    # 事实核对：镜像里确实没有 changelog（它也不能有：原始迁移没有幂等守卫，在 init.sql
    # 已建好的库上重放会因 CREATE TABLE 撞名而让 backend 启动失败）。
    assert not (_build(fake_repo)["out"] / "backend" / "changelog").exists()


FENCE = "```"


def _bash_blocks(readme: str) -> list[str]:
    """只取 ```bash 围栏里的内容。

    必须只扫围栏：README 正文合法地引用了那条被禁的一条命令，用来解释「为什么不能用它」。
    对全文做文本断言会被这段解释自己打中——与早先 compose 的 ${DEPLOY_ROOT} 断言被自己的
    注释误伤是同一个坑，结论也一样：先挑准判定域，再写断言。
    """
    return re.findall(re.escape(FENCE) + r"bash\n([\s\S]*?)" + re.escape(FENCE), readme)


def test_readme_publish_steps_never_mirror_the_whole_package(fake_repo):
    """回归（Blocker）：发布步骤里不得出现整目录的 `rsync --delete`。

    包里刻意不含 .env、apps/<slug>/deploy/、data/* 与 store/* 的内容，也不含服务器上现场
    新建的应用。`--delete` 的语义是「删掉目标里源没有的东西」，所以一条
    `rsync -a --delete build-output/ <root>/` 会在**每次发布**时把这四类现场产物全删掉，
    直接推翻「只补不覆盖」这条共识；其中三类在首次部署跑完就已经存在，第一次跑发布
    命令就中招，不是理论风险。
    """
    out = _build(fake_repo)["out"]
    readme = (out / "README-deploy.md").read_text(encoding="utf-8")
    blocks = _bash_blocks(readme)
    assert blocks, "一个 bash 块都没解析出来，判定域已失效（后面的断言会假绿）"

    forbidden = re.compile(r"rsync[^\n]*--delete[^\n]*\bbuild-output/\s")
    hits = [ln for b in blocks for ln in b.splitlines() if forbidden.search(ln)]
    assert hits == [], f"发布步骤里又出现了整目录 rsync --delete：{hits}"

    # 反向断言：解释「为什么不能用」的那段正文必须在场，否则下一个人会把它简化回去。
    assert "为什么不能用一条" in readme
    # 后果表说 `.env` 会被删——那它得先存在：首次部署那节必须确实建了它，否则那段警告是空的。
    assert "cp .env.example .env" in blocks[0], "§2 里没建 .env，后果表的第一行就没了前提"


def test_readme_publish_steps_are_split_per_directory(fake_repo):
    """发布四步必须逐字落在 bash 块里：各自的 --delete / --exclude 组合就是安全边界本身。

    逐字断言而不是「大概有 rsync」：少一个 --delete，被删的文件会在服务器上留成僵尸；
    --exclude 少一个前导斜杠，就会顺带排掉任何深度下叫 deploy 的目录（应用工程里也可能
    有 backend/app/deploy），镜像里少了它是**静默失效**——不报错，只是发布出来少一块。
    """
    readme = (_build(fake_repo)["out"] / "README-deploy.md").read_text(encoding="utf-8")
    cmd = "\n".join(_bash_blocks(readme))

    for required in (
            f"rsync -a --delete build-output/backend/  {ROOT}/backend/",
            f"rsync -a --delete build-output/frontend/ {ROOT}/frontend/",
            f"rsync -a --delete --exclude '/deploy/' build-output/apps/demo1/ {ROOT}/apps/demo1/",
            f"rsync -a --delete --exclude '/deploy/' build-output/apps/demo2/ {ROOT}/apps/demo2/",
    ):
        assert required in cmd, f"发布步骤缺了：{required}"
    assert "--exclude 'deploy/'" not in cmd, "deploy/ 的排除没锚在传输根上，会误伤同名子目录"

    # 顶层文件那一条必须**不带** --delete：.env 就在这一层。
    top = [ln for ln in cmd.splitlines() if "build-output/init.sql" in ln]
    assert top, "顶层文件的同步命令不见了"
    assert "--delete" not in top[0], f"顶层文件同步带了 --delete，会删掉 .env：{top[0]}"


def test_readme_bash_blocks_keep_line_continuations(fake_repo):
    """回归：多行命令的行尾反斜杠必须活着进产物，且不得带尾随空白。

    这些文本写在 Python 字符串字面量里，而字面量中行尾的单个 `\\` 是**续行符**：它与后面的
    换行一起被解释器吃掉，产物里的三行 rsync 会静默拼成一行。拼成一行恰好仍是合法 shell，
    所以打包不报错、别的断言也全绿——只有人对着 README 逐条核对同步范围时才发现命令糊成
    了一坨。带尾随空白的 `\\ ` 则相反，那是真的 shell 语法错误，一并守住。
    """
    readme = (_build(fake_repo)["out"] / "README-deploy.md").read_text(encoding="utf-8")
    cmd = "\n".join(_bash_blocks(readme))

    dangling = [ln for ln in cmd.splitlines() if re.search(r"\\\s+$", ln)]
    assert dangling == [], f"反斜杠后面还有空白，shell 会当成语法错误：{dangling}"

    top = [ln for ln in cmd.splitlines() if "build-output/init.sql" in ln]
    assert top, "顶层文件那条 rsync 整条不见了"
    assert top[0].endswith("\\"), f"续行反斜杠被字符串字面量吃掉了：{top[0]}"
    assert "build-output/README-deploy.md \\" in cmd, "第二行的续行同样丢了"


def test_package_really_omits_server_side_artifacts(fake_repo):
    """事实核对：README 声称「包里刻意不含这四类」，那就必须真的不含。

    这段声明是分目录上传方案的全部依据。哪天打包器把 .env 或 deploy/ 塞进了包，README 的
    后果表就成了假话，而 --exclude 也从「保命」悄悄变成「多余」——两边会一起失去意义。
    """
    out = _build(fake_repo)["out"]
    assert not (out / ".env").exists(), "包里出现了 .env（凭据一律不进包）"
    for slug in ("demo1", "demo2"):
        assert not (out / "apps" / slug / "deploy").exists(), f"{slug} 的 deploy/ 进了包"
    for shared in ("data", "store"):
        names = sorted(p.name for p in (out / shared).rglob("*"))
        assert names == [".gitkeep"], f"{shared}/ 里多了服务器现场才会有的东西：{names}"


def test_init_sql_header_count_matches_listed_rewrites(fake_repo):
    """边界：header 声称的改写处数必须与它自己列出的条目数一致。

    这句话是运维理解产物语义的入口，说「三处」却列了四条会让人怀疑自己漏读了一条
    （已经真发生过）。数字写死在文本里必然随改写规则增长而漂移，故按「列了几条」反推。
    """
    sql = _build(fake_repo)["out"].joinpath("init.sql").read_text(encoding="utf-8")
    head = sql.split("-- =====")[0]
    listed = re.findall(r"^--\s+\d\)", head, re.MULTILINE)
    cn = {"二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
    m = re.search(r"做了(.)处等价改写", head)
    assert m, "header 里找不到改写处数的表述"
    assert cn[m.group(1)] == len(listed), f"声称 {m.group(1)} 处，实际列了 {len(listed)} 条"
    # header 收尾还有一句同样的计数（「N 处均只存在于本生成物」），两处必须一致
    closing = re.search(r"(.)处均只存在于本生成物", head)
    assert closing, "header 里找不到收尾的处数表述"
    assert closing.group(1) == m.group(1), "header 开头与收尾声称的处数不一致"


def test_preset_seed_does_not_look_up_workspace_by_root_path(fake_repo):
    """回归：种子段不得靠 root_path 反查 workspace.id。

    V20260925_02 把 root_path 的全局 UNIQUE 换成了 (owner_user_id, root_path) 联合唯一，
    于是「同路径、不同属主」的两行是合法的；反查会命中多行，把同一个 slug 插两遍。
    """
    sql = (_build(fake_repo, slugs=("demo1",))["out"] / "init.sql").read_text(encoding="utf-8")
    assert "RETURNING id INTO" in sql
    assert "w.root_path =" not in sql


# ---------------------------------------------------------------- 重定向输出的编码
#
# Windows 上被重定向的流默认按本机代码页（cp936）编码，而 cp936 里没有 '✗'（U+2717），
# stderr 的 backslashreplace 会把它写成字面量 `\u2717`——日志里就成了乱码。
# --no-pause 就是为自动化开的，这条路径必须能把中文与符号完整写进日志。

class _FakeStream:
    """可配 tty 与可不可 reconfigure 的假流，用来隔离地观察编码决策。"""

    def __init__(self, *, tty: bool, can_reconfigure: bool = True, has_isatty: bool = True):
        self._tty = tty
        self._can_reconfigure = can_reconfigure
        self.calls: list[dict] = []
        if has_isatty:
            self.isatty = lambda: self._tty          # noqa: E731  假对象上直接挂最直观
        if can_reconfigure:
            self.reconfigure = self._record

    def _record(self, **kw):
        self.calls.append(kw)


def test_redirected_streams_are_widened_to_utf8(monkeypatch):
    out, err = _FakeStream(tty=False), _FakeStream(tty=False)
    monkeypatch.setattr(bo.sys, "stdout", out)
    monkeypatch.setattr(bo.sys, "stderr", err)
    bo._widen_redirected_streams()
    assert out.calls == [{"encoding": "utf-8"}]
    assert err.calls == [{"encoding": "utf-8"}]


def test_console_streams_are_left_alone(monkeypatch):
    """边界：交互控制台一律不碰——代码页由调用方（bat 的 chcp 65001）定，
    在这里强行改成 UTF-8 反而会和终端实际编码不一致，把本来正常的中文弄成乱码。"""
    out, err = _FakeStream(tty=True), _FakeStream(tty=True)
    monkeypatch.setattr(bo.sys, "stdout", out)
    monkeypatch.setattr(bo.sys, "stderr", err)
    bo._widen_redirected_streams()
    assert out.calls == [] and err.calls == []


def test_stream_without_reconfigure_does_not_crash(monkeypatch):
    """边界：被换成不支持 reconfigure 的对象（StringIO、已关闭的流）时只能忽略，
    不能因为一个日志编码优化把打包本身弄挂。"""
    out, err = _FakeStream(tty=False, can_reconfigure=False), _FakeStream(tty=False)
    monkeypatch.setattr(bo.sys, "stdout", out)
    monkeypatch.setattr(bo.sys, "stderr", err)
    bo._widen_redirected_streams()                    # 不抛就是过
    assert err.calls == [{"encoding": "utf-8"}]       # 能改的那个仍然改


def test_stream_without_isatty_is_treated_as_console(monkeypatch):
    """边界：分不出 tty 还是重定向时选择不碰——猜错方向的代价（乱码）比不优化更大。"""
    out = _FakeStream(tty=False, has_isatty=False)
    monkeypatch.setattr(bo.sys, "stdout", out)
    monkeypatch.setattr(bo.sys, "stderr", _FakeStream(tty=True))
    bo._widen_redirected_streams()
    assert out.calls == []



# ---------------------------------------------------------------- 失败不得毁掉上一个好包
#
# build() 里最可能失败的一步（synth_init_sql 的重放安全/幂等审计）在装配中段，
# 而旧实现是先 rmtree(out) 再装配——于是 changelog 一不合规，用户连上一个可部署的
# 包一起丢掉，而 build-output.bat 却告诉他「build-output was left untouched」。
# 下面这组盯的就是这句承诺必须是真的。

CHANGELOG_UNFIXABLE = """-- V20260105_01_unfixable.sql
CREATE TYPE mood AS ENUM ('sad', 'ok');
"""


def _leftovers(out: Path) -> list[str]:
    """暂存与备份目录一律不得残留：它们混在交付目录旁边，会被 rsync 一并传上服务器。"""
    return sorted(p.name for p in out.parent.iterdir()
                  if p.name.startswith(out.name) and p.name != out.name)


def test_failed_audit_preserves_previous_good_package(fake_repo):
    """边界：审计拒包时，上一个包必须原封不动（含内容，不只是目录还在）。"""
    out = _build(fake_repo)["out"]
    before = (out / "init.sql").read_bytes()

    (fake_repo / "changelog" / "V20260105_01_unfixable.sql").write_text(
        CHANGELOG_UNFIXABLE, encoding="utf-8")
    with pytest.raises(bo.BuildError, match="CREATE TYPE"):
        _build(fake_repo)

    assert out.is_dir(), "失败后交付目录不该被删掉"
    assert (out / "init.sql").read_bytes() == before, "上一个包的 init.sql 被改写了"
    assert (out / "docker-compose.yml").is_file()
    assert (out / "backend" / "app" / "main.py").is_file()
    assert (out / "apps" / "demo1" / "app.json").is_file()


def test_failed_audit_leaves_no_staging_leftovers(fake_repo):
    """边界：失败后不许留下暂存/备份目录（否则下一次 rsync 会把半成品传上服务器）。"""
    out = _build(fake_repo)["out"]
    (fake_repo / "changelog" / "V20260105_01_unfixable.sql").write_text(
        CHANGELOG_UNFIXABLE, encoding="utf-8")
    with pytest.raises(bo.BuildError):
        _build(fake_repo)
    assert _leftovers(out) == []


def test_failure_before_first_package_creates_no_output_dir(fake_repo):
    """边界：从未成功打过包时失败，连 out 目录都不应该存在（不留空壳让人误以为有包）。"""
    out = fake_repo / "build-output"
    (fake_repo / "changelog" / "V20260105_01_unfixable.sql").write_text(
        CHANGELOG_UNFIXABLE, encoding="utf-8")
    with pytest.raises(bo.BuildError):
        _build(fake_repo)
    assert not out.exists()
    assert _leftovers(out) == []


def test_deep_credential_scan_failure_preserves_previous_package(fake_repo, monkeypatch):
    """边界：失败点推到最深的出包终检（文件已全部写完），上一个包仍须完好。

    这是最苛刻的时机：暂存目录里已经有一份完整的（但带凭据的）包，
    回退必须丢掉它而不是把它当成产物交出去。
    """
    out = _build(fake_repo)["out"]
    before = (out / "init.sql").read_bytes()

    real = bo.copy_preset_app

    def leaky(src, dst, *args, **kw):
        n = real(src, dst, *args, **kw)
        if Path(dst).name == "demo1":
            (Path(dst) / ".env").write_text("DEV_PASSWORD=super-secret\n", encoding="utf-8")
        return n

    monkeypatch.setattr(bo, "copy_preset_app", leaky)
    with pytest.raises(bo.BuildError, match="\\.env"):
        _build(fake_repo)

    assert (out / "init.sql").read_bytes() == before
    assert not (out / "apps" / "demo1" / ".env").exists(), "带凭据的半成品被当成产物留下了"
    assert _leftovers(out) == []


def test_successful_rebuild_replaces_previous_without_leftovers(fake_repo):
    """成功路径：旧包被整目录换掉（上次残留不得混入），且无暂存目录遗留。"""
    out = _build(fake_repo)["out"]
    stale = out / "stale-from-last-run.txt"          # 模拟上一次跑留下的脏文件
    stale.write_text("junk", encoding="utf-8")

    out2 = _build(fake_repo)["out"]
    assert out2 == out
    assert not stale.exists(), "旧包的残留文件混进了新交付包"
    assert (out / "init.sql").is_file()
    assert _leftovers(out) == []


def test_stale_staging_from_crashed_run_is_reclaimed(fake_repo):
    """边界：上一次跑到一半被 Ctrl-C，暂存目录脏在那——下一次打包必须能自己清掉它。"""
    out = fake_repo / "build-output"
    crashed = out.parent / (out.name + ".staging")
    (crashed / "apps").mkdir(parents=True)
    (crashed / "apps" / "half-copied.txt").write_text("junk", encoding="utf-8")

    result = _build(fake_repo)
    assert (result["out"] / "init.sql").is_file()
    assert not crashed.exists(), "崩溃残留的暂存目录没被回收"
    assert _leftovers(out) == []


# ---------------------------------------------------------------- 换包本身失败
#
# 上面的组盯的是「装配阶段失败」；下面这组盯的是装配已成功、改名换包时才出事
# （目录被杀软 / 索引器 / IDE 文件监听短暂占用、磁盘满、权限不足）——在 Windows 上并不罕见。
#
# 真实事故记录：交付目录里的 docker-compose.yml 正被编辑器打开时，staging → out 这一步报了
# [WinError 5] 拒绝访问，整个打包失败，只能人工重跑（重跑即成功，坐实了是瞬时占用）。
# 所以这组用例的第一个要求就是：一次性抖动不得让打包失败。

def _flaky_replace(monkeypatch, fail_on_calls, exc):
    """把 bo.os.replace 换成「指定调用序号报错」，其余转给真实现。

    fail_on_calls 传单个序号模拟一次性抖动，传一组序号模拟占用持续了整个重试窗口。
    返回的 state["n"] 是总调用次数，用它断言重试确实是有界的（不会无限转下去）。
    """
    real = bo.os.replace
    targets = {fail_on_calls} if isinstance(fail_on_calls, int) else set(fail_on_calls)
    state = {"n": 0}

    def flaky(src, dst):
        state["n"] += 1
        if state["n"] in targets:
            raise exc
        return real(src, dst)

    monkeypatch.setattr(bo.os, "replace", flaky)
    return state


def _no_sleep(monkeypatch) -> list:
    """重试真的会 sleep，测试里不能等：换成计数器，顺便用来断言退避了几次。"""
    naps = []
    monkeypatch.setattr(bo.time, "sleep", naps.append)
    return naps


LOCKED = PermissionError(32, "另一个进程正在使用此文件")


def test_transient_lock_during_swap_is_retried_not_fatal(fake_repo, monkeypatch):
    """边界：staging → out 只抖动一次时，重试必须把它吃掉，打包照常成功。

    这就是上面记录的实测事故：占用是毫秒级的，一次失败不该让人重跑整个打包。
    """
    out = _build(fake_repo)["out"]
    naps = _no_sleep(monkeypatch)
    # 第 1 次 = out → .old（成功），第 2 次 = staging → out（抖动），第 3 次 = 重试成功
    _flaky_replace(monkeypatch, 2, LOCKED)
    result = _build(fake_repo)

    assert result["out"] == out
    assert (out / "init.sql").is_file(), "重试报成功了，交付目录却是空的"
    assert len(naps) == 1, f"应该只退避一次，实际 {len(naps)} 次"
    assert _leftovers(out) == [], "重试成功后 .old 与 .staging 都该被回收"


def test_swap_failure_restores_previous_package_and_raises_build_error(fake_repo, monkeypatch):
    """边界：占用持续不退时，旧包必须被放回去，而且报 BuildError 不是裸 traceback。

    裸 traceback 对双击 bat 的人来说只是一堆栈；而且它会让 bat 里的「上一个包没被碰到」
    变成一句空话——真正的后果是交付目录此刻根本不存在。
    """
    out = _build(fake_repo)["out"]
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    _no_sleep(monkeypatch)

    # 第 1 次 = out → .old；接着 _SWAP_RETRIES 次 staging → out 全部失败；最后一次是回退
    state = _flaky_replace(monkeypatch, range(2, 2 + bo._SWAP_RETRIES), LOCKED)
    with pytest.raises(bo.BuildError, match="换包失败"):
        _build(fake_repo)

    assert out.is_dir(), "旧包没被放回去，交付目录直接消失了"
    after = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert after == before, "上一个可部署的包被改动了"
    assert _leftovers(out) == []
    # 重试必须有界：第一步 1 次 + 换包 _SWAP_RETRIES 次 + 回退 1 次，一次不多
    assert state["n"] == bo._SWAP_RETRIES + 2, f"重试次数失控：共调用 {state['n']} 次"


def test_swap_failure_on_first_step_leaves_nothing_behind(fake_repo, monkeypatch):
    """边界：连第一步（out → .old）都失败时，也不能把暂存目录脏在交付目录旁边。

    这一步在旧实现里根本没被保护：它抛的是 OSError，既不会回退也不会清 staging，
    于是下一次 rsync 会把一份半成品（可能含尚未被终检拦下的凭据）传上服务器。
    """
    out = _build(fake_repo)["out"]
    before = (out / "init.sql").read_bytes()
    _no_sleep(monkeypatch)

    state = _flaky_replace(monkeypatch, range(1, 1 + bo._SWAP_RETRIES), LOCKED)
    with pytest.raises(bo.BuildError, match="换包失败"):
        _build(fake_repo)

    assert (out / "init.sql").read_bytes() == before, "旧包在第一步失败时被弄坏了"
    assert _leftovers(out) == [], "第一步就失败了，.old 压根不应该存在"
    assert state["n"] == bo._SWAP_RETRIES, f"第一步重试次数失控：{state['n']}"


def test_non_permission_oserror_during_swap_is_not_retried(fake_repo, monkeypatch):
    """边界：磁盘满这类错误重试多少次都一样，必须立刻失败，不能白等两秒。

    退避只对「瞬时占用」有意义；把它用在所有 OSError 上，只会让真正的失败信息晚出现，
    还会让人误以为这是可以重跑摸过去的偶发抖动。
    """
    out = _build(fake_repo)["out"]
    naps = _no_sleep(monkeypatch)
    state = _flaky_replace(monkeypatch, 2, OSError(28, "No space left on device"))
    with pytest.raises(bo.BuildError, match="换包失败"):
        _build(fake_repo)

    assert naps == [], "对不可恢复的错误做了退避重试"
    # 第 1 次 = out → .old，第 2 次 = staging → out 立即失败，第 3 次 = 回退；没有重试
    assert state["n"] == 3, f"磁盘满也被重试了：共调用 {state['n']} 次"
    assert out.is_dir() and (out / "init.sql").is_file(), "磁盘满把旧包弄丢了"
    assert _leftovers(out) == []


def test_rollback_failure_is_reported_honestly(fake_repo, monkeypatch):
    """边界：连回退都失败时，报错不能照旧说「已原样放回」——那时交付目录根本不存在。

    照原话报会让运维以为 build-output 里还是旧内容；旧包实际被留在 .old 里，必须把这
    一点说出来，否则演示现场会对着一个不存在的目录排查半天。
    """
    out = _build(fake_repo)["out"]
    before = (out / "init.sql").read_bytes()
    _no_sleep(monkeypatch)

    # 换包的 _SWAP_RETRIES 次全失败，紧接着回退也要把它自己的 _SWAP_RETRIES 次全失败
    # （回退同样会重试：占用既然能打断换包，就能打断回退）。
    state = _flaky_replace(monkeypatch, range(2, 2 + 2 * bo._SWAP_RETRIES), LOCKED)
    with pytest.raises(bo.BuildError, match="回退也失败了"):
        _build(fake_repo)

    old = out.parent / (out.name + ".old")
    assert not out.exists(), "回退失败了，交付目录却还在（那报错里的警告就是假话）"
    assert (old / "init.sql").read_bytes() == before, "旧包在 .old 里也丢了"
    assert state["n"] == 1 + 2 * bo._SWAP_RETRIES, f"调用次数不符预期：{state['n']}"


def test_first_ever_build_swap_failure_does_not_claim_rollback(fake_repo, monkeypatch):
    """边界：首次打包（本来就没有旧包）时换包失败，文案不得宣称「回退失败、旧包在 .old」。

    那时 .old 压根不存在。照原话报会让人去一个不存在的目录里找「上一个可部署的包」，而真相
    是这台机器上从来没有过交付包，重跑一次就行——排查方向被指反，代价是演示前的十几分钟。
    """
    out = fake_repo / "build-output"
    assert not out.exists(), "前置条件：这是首次打包"
    _no_sleep(monkeypatch)
    state = _flaky_replace(monkeypatch, range(1, 1 + bo._SWAP_RETRIES), LOCKED)
    with pytest.raises(bo.BuildError) as ei:
        _build(fake_repo)

    msg = str(ei.value)
    assert "本次之前没有可用的交付包" in msg
    assert "回退也失败了" not in msg and "已原样放回" not in msg, f"首次打包没有旧包可回退：{msg}"
    assert "已对瞬时占用连续尝试" in msg, "占用类错误确实重试过，这一点要说实话"
    assert not out.exists() and _leftovers(out) == []
    assert state["n"] == bo._SWAP_RETRIES


def test_unrecoverable_swap_error_message_does_not_claim_retry(fake_repo, monkeypatch):
    """边界：磁盘满这类一次都没重试，文案就不能说「已对瞬时占用连续尝试 N 次」。

    说了会让人以为「再跑一次也许就过」，而磁盘满重跑多少次都是同一个结果；真正该做的是腾
    空间。同时旧包确实被放回去了，这一条也得照实说，不能因为换了分支就含糊过去。
    """
    out = _build(fake_repo)["out"]
    _no_sleep(monkeypatch)
    _flaky_replace(monkeypatch, 2, OSError(28, "No space left on device"))
    with pytest.raises(bo.BuildError) as ei:
        _build(fake_repo)

    msg = str(ei.value)
    assert "不可重试的错误，未重试" in msg
    assert "已对瞬时占用连续尝试" not in msg, f"没重试却宣称重试过：{msg}"
    assert "上一个交付包已原样放回" in msg
    assert out.is_dir() and (out / "init.sql").is_file()


def test_filesystem_error_surfaces_as_build_error_not_traceback(fake_repo, monkeypatch):
    """边界：装配阶段的文件系统错误同样要收成 BuildError（磁盘满、路径过长、杀软锁文件）。

    main() 只接 BuildError；其余异常会打成裸 traceback，而 bat 只能告诉用户「失败了」。
    """
    real_copy = bo.copy_backend

    def full_disk(*args, **kw):
        real_copy(*args, **kw)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bo, "copy_backend", full_disk)
    with pytest.raises(bo.BuildError, match="文件系统"):
        _build(fake_repo)
    assert _leftovers(fake_repo / "build-output") == []


# ---------------------------------------------------------------- 网关域名注入
#
# 控制台里 https://{slug}.<域名> 这个地址由 app/deploy/nics.py 在**容器内**读
# ZEROCODE_GATEWAY_DOMAIN 拼出，读不到就回退 zerocode.com。打包器不产出这个变量时，交付包
# 在任何真域名上都会显示成 zerocode.com——ADR-0016 记过的症状：地址看着正常、点进去全 404，
# 而日志里没有任何一行指向真因。
# 下面这组盯的是「打包时把域名带进包」这条链路：取值优先级、贯通三处文档、与部署根相反的
# 可覆盖性，以及值的校验（非法域名必须在装配之前拒掉）。

GATEWAY_ENV = "ZEROCODE_GATEWAY_DOMAIN"


def _backend_env(out: Path) -> dict:
    """按 YAML 结构取 backend.environment，不做文本包含断言。

    compose 的注释里合法地写着变量名本身，全文本断言会被自己的注释误伤——与 zcnet 的
    external 断言是同一个坑。
    """
    doc = yaml.safe_load((out / "docker-compose.yml").read_text(encoding="utf-8"))
    return doc["services"]["backend"]["environment"]


def _env_example_line(out: Path) -> str:
    lines = (out / ".env.example").read_text(encoding="utf-8").splitlines()
    hits = [ln for ln in lines if ln.startswith(GATEWAY_ENV + "=")]
    assert len(hits) == 1, f".env.example 里 {GATEWAY_ENV} 应恰好一行，实际 {hits}"
    return hits[0]


def test_compose_injects_gateway_domain_into_backend_env(fake_repo):
    """backend 容器必须拿到域名，否则 nics.py 静默回退到 zerocode.com。"""
    out = _build(fake_repo, gateway_domain="leerysink.com")["out"]
    assert _backend_env(out)[GATEWAY_ENV] == "${" + GATEWAY_ENV + ":-leerysink.com}"


def test_gateway_domain_falls_back_to_bundled_default(fake_repo, monkeypatch):
    """既不给参数也没有打包机环境变量时，行为必须与加这个功能之前逐字一致。"""
    monkeypatch.delenv(GATEWAY_ENV, raising=False)
    s = _build(fake_repo)
    assert s["gateway_domain"] == bo.DEFAULT_GATEWAY_DOMAIN == "zerocode.com"
    assert _backend_env(s["out"])[GATEWAY_ENV] == "${" + GATEWAY_ENV + ":-zerocode.com}"


def test_gateway_domain_taken_from_packaging_machine_env(fake_repo, monkeypatch):
    """打包机环境变量这一层是给「下次我还要这么搞」的：setx 一次，之后每次发布自动带上。"""
    monkeypatch.setenv(GATEWAY_ENV, "leerysink.com")
    s = _build(fake_repo)
    assert s["gateway_domain"] == "leerysink.com"
    assert _backend_env(s["out"])[GATEWAY_ENV] == "${" + GATEWAY_ENV + ":-leerysink.com}"


def test_explicit_flag_beats_packaging_machine_env(fake_repo, monkeypatch):
    monkeypatch.setenv(GATEWAY_ENV, "env-side.com")
    assert _build(fake_repo, gateway_domain="flag-side.com")["gateway_domain"] == "flag-side.com"


def test_invalid_packaging_machine_env_fails_loudly(fake_repo, monkeypatch):
    """环境变量里的坏值不许被忽略：它是打包机上的长期设置，静默降级等于以后每次发布都错。"""
    monkeypatch.setenv(GATEWAY_ENV, "http://leerysink.com")
    with pytest.raises(bo.BuildError, match="网关域名"):
        _build(fake_repo)


@pytest.mark.parametrize("raw", ["", "   "])
def test_explicit_empty_domain_is_rejected_not_defaulted(fake_repo, raw):
    """显式传空必须报错，不能悄悄退回默认值。

    `--slugs ""` 的空是有意义的（不打预置应用），域名的空只可能是手滑或 bat 里漏了值：
    退回 zerocode.com 等于把这次发布的地址全写错，而打包还报成功。要默认值就别传这个参数。
    """
    with pytest.raises(bo.BuildError, match="网关域名"):
        _build(fake_repo, gateway_domain=raw)


@pytest.mark.parametrize("raw", ["", "  "])
def test_blank_env_var_counts_as_unset_not_as_invalid(fake_repo, monkeypatch, raw):
    """与环境变量相反：空值按「没设」处理。

    shell / CI 里 `set FOO=` 与「没设」通行等价，在这里报错会让本来正常的打包被一个
    无关的空变量拦下。显式传参是人的动作（漏值要拦），继承来的空值不是。
    """
    monkeypatch.setenv(GATEWAY_ENV, raw)
    assert _build(fake_repo)["gateway_domain"] == bo.DEFAULT_GATEWAY_DOMAIN


@pytest.mark.parametrize("raw", [
    "leerysink.com", "LeerySink.COM", "  leerysink.com  ", "a-b.c-d.com",
    "xn--fiqs8s.com", "lvh.me", "demo.leerysink.cn", "127.0.0.1.nip.io",
])
def test_normalize_gateway_domain_accepts_and_normalizes(raw):
    got = bo.normalize_gateway_domain(raw)
    assert got == raw.strip().lower()
    assert bo.normalize_gateway_domain(got) == got          # 幂等：规范化过的值再过一次不变


DOMAIN_REJECTS = [
    "zerocode",                       # 只有一个标签
    "com",
    "localhost",                      # 单标签一律拒：*.localhost 的通配证书走不通
    ".leerysink.com",                 # 前导点
    "leerysink.com.",                 # 尾点
    "leerysink..com",                 # 空标签
    "-a.leerysink.com",               # 标签以连字符开头
    "a-.leerysink.com",               # 标签以连字符结尾
    "a_b.leerysink.com",              # 下划线不是合法主机名字符
    "a b.leerysink.com",              # 空格
    "http://leerysink.com",           # 带协议
    "leerysink.com/path",             # 带路径
    "user@leerysink.com",             # 带 userinfo
    "leerysink.com:8443",             # 带端口
    "*.leerysink.com",                # 通配符本身：要签的是泛解析证书，不是把这个当域名
    "${ZEROCODE_GATEWAY_DOMAIN}",     # compose 插值语法，会把生成的 yml 打断
    "leerysink{com}",
    "leerysink.com$",
    "a`b.com",
    "a\"b.com",
    "例子.com",                        # 非 ASCII：nics.py 与网关 lua 都不做 punycode
    "\uff4c\uff45\uff45\uff0e\uff43\uff4f\uff4d",  # 全角
    "1.2.3.4",                        # IP 形态：拼出来是 https://demo1.1.2.3.4
]


@pytest.mark.parametrize("raw", DOMAIN_REJECTS)
def test_normalize_gateway_domain_rejects(raw):
    with pytest.raises(bo.BuildError, match="网关域名"):
        bo.normalize_gateway_domain(raw)


def test_rejected_domain_message_shows_the_bad_value():
    """报错必须带上原值：只说「域名非法」运维无从判断是哪一段被吃掉了。"""
    with pytest.raises(bo.BuildError) as e:
        bo.normalize_gateway_domain("http://leerysink.com")
    assert "http://leerysink.com" in str(e.value)


def test_normalize_gateway_domain_label_length_boundary():
    """DNS 单标签上限 63：恰好 63 必须过，64 必须拒。"""
    bo.normalize_gateway_domain("a" * 63 + ".com")
    with pytest.raises(bo.BuildError, match="63"):
        bo.normalize_gateway_domain("a" * 64 + ".com")


def test_normalize_gateway_domain_total_length_boundary():
    """整名上限 253：253 过、254 拒，且拒的原因只能是总长（每段都合规）。"""
    at_limit = ("a" * 63 + ".") * 3 + "b" * 61
    assert len(at_limit) == 253
    bo.normalize_gateway_domain(at_limit)
    over = at_limit + "b"
    assert len(over) == 254
    with pytest.raises(bo.BuildError, match="253"):
        bo.normalize_gateway_domain(over)


def test_gateway_domain_flows_into_all_three_documents(fake_repo):
    """同一个值得同时出现在 compose、.env.example 与 README 里，缺一个就必然漂移。"""
    out = _build(fake_repo, gateway_domain="leerysink.com")["out"]
    assert _backend_env(out)[GATEWAY_ENV].endswith("leerysink.com}")
    assert _env_example_line(out) == f"{GATEWAY_ENV}=leerysink.com"
    readme = (out / "README-deploy.md").read_text(encoding="utf-8")
    assert "leerysink.com" in readme


def test_gateway_domain_stays_runtime_overridable_unlike_deploy_root(fake_repo):
    """与部署根的处置**刻意相反**，所以两边都得钉住。

    部署根钉死：compose 挂载、镜像 WORKDIR、init.sql 的 root_path 三处同源，`.env` 里改只会
    让它们对不上且全程不报错。网关域名相反——README 承诺后续发布不覆盖服务器上的 `.env`，
    值只有落在 `.env` 里才活得过下一次发布；compose 写成裸值就等于在服务器上永远改不动它。
    """
    out = _build(fake_repo, gateway_domain="leerysink.com")["out"]
    value = _backend_env(out)[GATEWAY_ENV]
    assert value == "${" + GATEWAY_ENV + ":-leerysink.com}", f"不再是运行期可覆盖形态：{value}"
    # .env.example 反过来必须是裸值：compose 的 .env 不做二次插值，${...} 会原样进容器
    assert "${" not in _env_example_line(out)


def test_readme_documents_the_packaged_gateway_instead_of_denying_it(fake_repo):
    """网关已入包（ADR-0018），README 必须给出启动方式，而不是继续写「本包不含 gateway/」。

    文档与包内容相反时，排查会被引向「网关没部署」，而真实故障可能是证书、域名不一致或
    应用还没 running。保留「点进去全 404」这条症状，但排障方向必须指向包里真实存在的组件。
    """
    out = _build(fake_repo, gateway_domain="leerysink.com")["out"]
    readme = (out / "README-deploy.md").read_text(encoding="utf-8")
    assert "网关域名" in readme and "leerysink.com" in readme
    assert "本包不含 gateway" not in readme, "README 否定了包内实际存在的 gateway/"
    assert "gateway/docker-compose.yml up -d" in readme, "没写网关怎么启动"
    assert "gateway/certs/" in readme, "没写证书落在哪"
    assert "点进去全 404" in readme            # 排障表里要有这条症状
    assert (out / "gateway" / "docker-compose.yml").is_file()


# ---------------------------------------------------------------- 网关入包（ADR-0018）
#
# 网关从「另行部署」变为交付包的一部分，但包袱里的**凭据与运行期状态**仍然不外发：
# certs/ 是 TLS 私钥，routes.json 是服务器运行期投影。下面这组同时钉住「该带的带齐」
# 与「不该带的一个字节都没有」，因为后者漏一次就是私钥泄露。

def _gateway_service(out: Path) -> dict:
    doc = yaml.safe_load((out / "gateway" / "docker-compose.yml").read_text(encoding="utf-8"))
    return doc["services"]["gateway"]


def test_gateway_configs_are_shipped(fake_repo):
    out = _build(fake_repo)["out"]
    for rel in ("gateway/docker-compose.yml", "gateway/nginx.conf", "gateway/lua/router.lua",
                "gateway/404.html", "gateway/make-cert.sh", "gateway/make-cert.ps1",
                "gateway/.env.example", "gateway/routes/routes.json"):
        assert (out / rel).is_file(), f"网关配置缺 {rel}"


def test_gateway_private_key_and_live_routes_never_ship(fake_repo):
    """私钥、现场路由与本地 .env 一样都不许进包——与预置应用的 deploy/ 是同一类禁区。"""
    out = _build(fake_repo)["out"]
    assert sorted(p.name for p in (out / "gateway" / "certs").iterdir()) == [".gitkeep"]
    assert not list((out / "gateway").rglob("*.key"))
    assert not list((out / "gateway").rglob("*.crt"))
    assert not (out / "gateway" / ".env").exists()
    assert (out / "gateway" / "routes" / "routes.json").read_text(encoding="utf-8") == "{}\n"
    leaked = [p.relative_to(out).as_posix() for p in out.rglob("*")
              if p.is_file() and ("PRIVATE-KEY-MUST-NOT-SHIP" in p.read_text(encoding="utf-8", errors="ignore")
                                  or "dev-only" in p.read_text(encoding="utf-8", errors="ignore"))]
    assert leaked == [], f"服务器现场产物混进包：{leaked}"


def test_gateway_defaults_baked_into_gateway_compose(fake_repo):
    """不建 gateway/.env 也要与平台指向同一域名、并在 Linux 上连得到宿主端口。"""
    out = _build(fake_repo, gateway_domain="leerysink.com")["out"]
    env = _gateway_service(out)["environment"]
    assert env["GATEWAY_DOMAIN"] == "${" + GATEWAY_ENV + ":-leerysink.com}"
    # 上游烘成 IP 字面量：Linux 上 nginx 只查 resolver、不读 /etc/hosts，
    # host.docker.internal 会 NXDOMAIN → 所有应用域名 502（2026-10-29 演示机实测）。
    assert env["UPSTREAM_HOST"] == "${ZEROCODE_GATEWAY_UPSTREAM:-172.17.0.1}"
    example = (out / "gateway" / ".env.example").read_text(encoding="utf-8")
    assert f"{GATEWAY_ENV}=leerysink.com" in example
    assert "ZEROCODE_GATEWAY_UPSTREAM=172.17.0.1" in example
    assert "gateway/ 下" in example or "同目录的 .env" in example


def test_repo_gateway_upstream_keeps_desktop_default():
    """仓库版本服务本机 Docker Desktop，默认值必须留在 host.docker.internal；
    出包版本才换成 172.17.0.1（见 test_gateway_defaults_baked_into_gateway_compose）。
    两边搞反时：本机网关全 502，或服务器上重新踩一遍 DNS 解析不了的老坑。"""
    text = (bo.REPO_ROOT / "gateway" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "UPSTREAM_HOST: ${ZEROCODE_GATEWAY_UPSTREAM:-host.docker.internal}" in text


def test_gateway_compose_shape_drift_fails_closed(fake_repo):
    """源文件里认不出域名 fallback 时必须拒包：替换静默不生效就是发一份错域名的包。"""
    (fake_repo / "gateway" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(bo.BuildError, match=GATEWAY_ENV):
        _build(fake_repo)
    assert not (fake_repo / "build-output").exists()


def test_platform_compose_shares_gateway_routes_directory_with_backend(fake_repo):
    """后端写 routes、网关读 routes，只有挂**同一个宿主目录**才成立。

    挂目录而不是单个文件：后端用 `.tmp + os.replace` 原子替换，单文件 bind mount 在替换后
    仍钉着旧 inode，网关会永远读到第一版路由。
    """
    out = _build(fake_repo)["out"]
    backend = yaml.safe_load((out / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["backend"]
    routes_file = f"{ROOT}/gateway/routes/routes.json"
    assert backend["environment"]["ZEROCODE_GATEWAY_ROUTES"] == routes_file
    assert f"{ROOT}/gateway/routes:{ROOT}/gateway/routes" in backend["volumes"]
    assert routes_file + ":" + routes_file not in backend["volumes"], "单文件挂载会被原子替换坑死"
    # 网关读的是 gateway/ 下的 ./routes —— 同一个宿主目录
    assert "./routes:/etc/nginx/routes:ro" in _gateway_service(out)["volumes"]


def test_dockerignore_keeps_gateway_out_of_the_image_context(fake_repo):
    """网关是宿主上的独立容器，配置文件由它自己挂载，不该被烘进平台镜像。"""
    out = _build(fake_repo)["out"]
    assert "\ngateway\n" in (out / ".dockerignore").read_text(encoding="utf-8")


def test_default_gateway_domain_matches_platform_and_gateway_fallback():
    """打包默认值必须与两处 env 回退值逐字相同：不一致时症状是「只改对了一半」。

    打包器与 nics.py、网关 compose 分属三个目录，谁都不校验谁。默认值漂开之后，不带参数
    出的包写的域名与容器里读不到 env 时的回退值不同，两边各自都「看着对」。
    """
    nics = (bo.REPO_ROOT / "app" / "deploy" / "nics.py").read_text(encoding="utf-8")
    m = re.search(r'os\.environ\.get\("ZEROCODE_GATEWAY_DOMAIN",\s*"([^"]*)"\)', nics)
    assert m, "nics.py 不再是 os.environ.get 带字面默认值的形态，本守卫的判定域已失效"
    assert m.group(1) == bo.DEFAULT_GATEWAY_DOMAIN
    gw = yaml.safe_load((bo.REPO_ROOT / "gateway" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert gw["services"]["gateway"]["environment"]["GATEWAY_DOMAIN"] == (
        "${" + GATEWAY_ENV + ":-" + bo.DEFAULT_GATEWAY_DOMAIN + "}")


def test_bat_forwards_gateway_domain_instead_of_swallowing_it():
    """bat 必须把 --gateway-domain 原样转发给 python，别当成自己的开关。

    参数循环里 --skip-frontend / --no-pause 是被吃掉后设内部变量的；哪天有人给 --gateway-domain
    也加一个分支，值就到不了 argparse，而 bat 照样「打包成功」——包里的域名还是默认的
    zerocode.com。这是最难查的一类静默失效，所以盯的是「解析段里不许出现这个旗标」。
    """
    bat = (bo.REPO_ROOT / "build-output.bat").read_text(encoding="utf-8")
    assert ":parse_args" in bat and ":args_done" in bat, "bat 参数循环的标记不见了，判定域失效"
    block = bat[bat.index(":parse_args"):bat.index(":args_done")]
    assert "--gateway-domain" not in block, f"bat 把 --gateway-domain 当自己的开关吃了：{block}"
    assert "build-output.bat --gateway-domain" in bat, "header 用法区没写这个参数"


def test_bad_gateway_domain_preserves_previous_package(fake_repo, monkeypatch):
    """边界：非法域名必须在装配之前拒掉——上一个好包一个字节都不能变。

    校验放到模板渲染之后的话，坏值会先在暂存目录里渲染成一份畸形 compose 再炸：轻则留下
    半成品被 rsync 一起传上服务器，重则（已经换过包）把可部署的旧包一起毁掉。
    """
    monkeypatch.delenv(GATEWAY_ENV, raising=False)
    out = _build(fake_repo, gateway_domain="leerysink.com")["out"]
    names = ("docker-compose.yml", ".env.example", "README-deploy.md", "init.sql")
    before = {n: (out / n).read_bytes() for n in names}
    with pytest.raises(bo.BuildError, match="网关域名"):
        _build(fake_repo, gateway_domain="leerysink.com/path")
    for n in names:
        assert (out / n).read_bytes() == before[n], f"{n} 被失败的打包改写了"
    assert _leftovers(out) == []


def test_first_build_with_bad_domain_creates_no_output_dir(fake_repo, monkeypatch):
    """边界：从未成功打过包时，坏域名不许留下空壳目录（会被当成「包已生成」误传上服务器）。"""
    monkeypatch.delenv(GATEWAY_ENV, raising=False)
    with pytest.raises(bo.BuildError, match="网关域名"):
        _build(fake_repo, gateway_domain="a_b.com")
    assert not (fake_repo / "build-output").exists()


# ---------------------------------------------------------------- 依赖镜像源注入
#
# 「本地好、服务器坏」的成因：换源约定活在打包机用户级（frontend/package-lock.json 的 resolved
# 全指向镜像站、本机 pip.conf 与 .npmrc），服务器容器一个都继承不到。下面这组盯的是「打包时
# 把镜像源烘进 Dockerfile / compose / .env.example」这条链路：零注入默认、单给一个、全给、
# 非法值拦截、打包机 env 继承、显式空被拒、坏值不改旧包。

APT_ENV = "ZEROCODE_APT_MIRROR"
PYPI_ENV = "ZEROCODE_PYPI_MIRROR"
NPM_ENV = "ZEROCODE_NPM_REGISTRY"


@pytest.fixture(autouse=True)
def _neutralize_host_packaging_env(monkeypatch):
    """每个用例起手清掉打包机上的四个 ZEROCODE_* 取值位。

    这四个变量是「三级取值」的中间那级（显式参数 > 打包机 env > 默认），而打包功能本身就鼓励
    「配一次以后每次发布自动带上」——于是配过它们的机器上，pytest 进程会继承这份用户级变量，
    把所有「不给就零注入 / 产物逐字不变」的断言莫名打红（实测：setx 之后 test_apt_mirror_rewrites…
    与 test_pypi_mirror_injects… 等三条同时红，而换台机器全绿）。需要取值的用例自己 setenv，
    monkeypatch 的时序在本清除之后，覆盖得了。
    """
    for name in (GATEWAY_ENV, APT_ENV, PYPI_ENV, NPM_ENV):
        monkeypatch.delenv(name, raising=False)


def test_default_mirrors_inject_nothing_into_dockerfile_or_compose(fake_repo, monkeypatch):
    """三个镜像源都不传时，Dockerfile 与 compose 的注入位必须与加这个功能之前逐字一致。

    这是 mirror 参数组的默认承诺：零注入。gateway 域是恒输出的（与 mirror 无关），所以
    本用例只盯 mirror 相关的三个注入位。
    """
    for e in (APT_ENV, PYPI_ENV, NPM_ENV):
        monkeypatch.delenv(e, raising=False)
    out = _build(fake_repo)["out"]
    dockerfile = (out / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "deb.debian.org" not in dockerfile          # apt 换源块未注入
    assert "PIP_INDEX_URL" not in dockerfile           # pip 镜像未注入
    compose_text = (out / "docker-compose.yml").read_text(encoding="utf-8")
    assert "PIP_INDEX_URL" not in compose_text         # compose 运行期 pip 未注入
    assert "NPM_CONFIG_REGISTRY" not in compose_text   # compose 运行期 npm 未注入
    # 字节级的零注入：注入位不许平白留出一个空行（换源块占独立模板行就会这样）
    assert "FROM python:3.12-slim\n# docker CLI" in dockerfile
    assert "https://download.docker.com/linux/debian" in dockerfile   # docker CE 仓库也未换源


def test_apt_mirror_rewrites_both_source_hosts_in_dockerfile(fake_repo):
    """apt 镜像必须同时替换 deb.debian.org 与 security.debian.org，且 docker-ce 路径跟随。

    断言盯的是 sed 行里的**目标主机**，不是「官方主机名整串消失」：换源靠的是构建期 sed，
    源主机名必然作为 sed 的匹配模式留在 Dockerfile 里（它是指令的一部分，不是配置值）。
    security.debian.org 不以 deb.debian.org 为子串，少一条 sed 就是「主源通了、安全源依旧超时」。
    """
    s = _build(fake_repo, apt_mirror="mirrors.aliyun.com")
    dockerfile = (s["out"] / "backend" / "Dockerfile").read_text(encoding="utf-8")
    # 两种源格式都得扫到：Debian 12+ 是 deb822 的 .sources，老镜像仍是 sources.list，只改一处就是半死
    assert "/etc/apt/sources.list /etc/apt/sources.list.d/*.sources" in dockerfile
    assert "s|deb.debian.org|mirrors.aliyun.com|g" in dockerfile
    assert "s|security.debian.org|mirrors.aliyun.com|g" in dockerfile
    assert "mirrors.aliyun.com/docker-ce/linux/debian" in dockerfile
    assert "download.docker.com" not in dockerfile     # 真正的配置值：docker CE 仓库已换主机
    assert "PIP_INDEX_URL" not in dockerfile           # 只给了 apt，pip 不注入
    assert s["apt_mirror"] == "mirrors.aliyun.com"


def test_pypi_mirror_injects_env_into_dockerfile(fake_repo):
    """PyPI 镜像烘进 Dockerfile 的 ENV PIP_INDEX_URL，构建期与容器内后续 pip 都走这里。"""
    url = "https://mirrors.aliyun.com/pypi/simple"
    out = _build(fake_repo, pypi_mirror=url)["out"]
    dockerfile = (out / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert f"PIP_INDEX_URL={url}" in dockerfile
    assert "deb.debian.org" not in dockerfile          # 只给了 pypi，apt 不注入


def test_npm_registry_flows_into_env_example(fake_repo):
    """npm 镜像源写进 .env.example（裸值）与 compose（可覆盖形态），运维可在服务器改 .env 覆盖。"""
    url = "https://registry.npmmirror.com"
    out = _build(fake_repo, npm_registry=url)["out"]
    env_example = (out / ".env.example").read_text(encoding="utf-8")
    assert f"{NPM_ENV}={url}" in env_example
    assert "${" not in env_example, (
        ".env.example 里不许出现 ${…}：compose 只插值一层，"
        "`${…}` 会原样进容器被当成字面值")
    compose_text = (out / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"{NPM_ENV}: ${{{NPM_ENV}:-{url}}}" in compose_text
    assert "PIP_INDEX_URL" not in env_example           # 只给了 npm，pypi 不注入


def test_only_one_mirror_leaves_others_untouched(fake_repo):
    """只给其中一个，另外两个的注入位必须字节不变。"""
    url = "https://mirrors.aliyun.com/pypi/simple"
    s = _build(fake_repo, pypi_mirror=url)
    out = s["out"]
    dockerfile = (out / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert f"PIP_INDEX_URL={url}" in dockerfile
    assert "deb.debian.org" not in dockerfile           # apt 未注入换源块
    assert "https://download.docker.com/linux/debian" in dockerfile  # docker CE 仓库未跟随
    env_example = (out / ".env.example").read_text(encoding="utf-8")
    assert NPM_ENV not in env_example                   # npm 未注入
    assert s["pypi_mirror"] == url
    assert s["apt_mirror"] is None
    assert s["npm_registry"] is None


@pytest.mark.parametrize("raw", [
    "mirrors.aliyun.com/path",      # apt 只接受裸主机名，带路径拒（路径必须沿用官方目录）
    "https://mirrors.aliyun.com/pypi/simple",   # 把 pip 镜像地址喂给 apt：带路径，拒
    "mirrors.aliyun.com:8080",      # 带端口拒
    "mirrors aliyun com",           # 含空白拒
    "  ",                           # 纯空白拒（要用官方源就别传这个参数）
])
def test_invalid_apt_mirror_rejected(raw):
    with pytest.raises(bo.BuildError, match="apt"):
        bo.normalize_apt_mirror(raw)


@pytest.mark.parametrize("raw,expect", [
    ("mirrors.aliyun.com", "mirrors.aliyun.com"),
    ("https://mirrors.aliyun.com", "mirrors.aliyun.com"),   # 协议剥掉：sed 只替换主机名段
    ("http://mirrors.aliyun.com/", "mirrors.aliyun.com"),   # 尾斜杠也剥掉
    ("MIRRORS.ALIYUN.COM", "mirrors.aliyun.com"),           # DNS 不区分大小写，归一为小写
])
def test_apt_mirror_normalizes_to_bare_host(raw, expect):
    """apt 镜像源归一为裸主机名：手滑粘了协议/尾斜杠/大写都能用，带路径才是真错。"""
    assert bo.normalize_apt_mirror(raw) == expect


@pytest.mark.parametrize("raw", [
    "mirrors.aliyun.com/pypi/simple",     # 不带协议
    "ftp://mirrors.aliyun.com/pypi",      # 非 http(s)
    "https://mirrors.aliyun.com?token=x", # 带查询串（凭据风险）
    "https://",                            # 无主机
    "  ",                                  # 纯空白
])
def test_invalid_pypi_mirror_rejected(raw):
    with pytest.raises(bo.BuildError, match="PyPI"):
        bo.normalize_pypi_mirror(raw)


@pytest.mark.parametrize("raw", [
    "registry.npmmirror.com",               # 不带协议
    "https://registry.npmmirror.com/",      # 尾斜杠应被规范化掉（不拒，但结果无尾斜杠）
])
def test_npm_registry_normalizes_or_rejects(raw):
    """带协议的正常值过；尾斜杠去掉；不带协议的拒。"""
    if "://" in raw:
        got = bo.normalize_npm_registry(raw)
        assert not got.endswith("/")
    else:
        with pytest.raises(bo.BuildError, match="npm"):
            bo.normalize_npm_registry(raw)


@pytest.mark.parametrize("env_name", [APT_ENV, PYPI_ENV, NPM_ENV])
def test_explicit_empty_mirror_rejected(fake_repo, env_name):
    """显式传空串必须报错，不能悄悄退回默认值（与网关域名的处置一致）。"""
    flag = {APT_ENV: "apt_mirror", PYPI_ENV: "pypi_mirror", NPM_ENV: "npm_registry"}[env_name]
    with pytest.raises(bo.BuildError):
        _build(fake_repo, **{flag: ""})


@pytest.mark.parametrize("env_name,value,flag,expect", [
    (APT_ENV, "mirrors.aliyun.com", "apt_mirror", "mirrors.aliyun.com"),
    (PYPI_ENV, "https://pypi.tuna.tsinghua.edu.cn/simple", "pypi_mirror",
     "https://pypi.tuna.tsinghua.edu.cn/simple"),
    (NPM_ENV, "https://registry.npmmirror.com", "npm_registry",
     "https://registry.npmmirror.com"),
])
def test_mirror_env_var_inherited(fake_repo, monkeypatch, env_name, value, flag, expect):
    """打包机 env 继承：setx 一次，之后每次发布自动带上。"""
    monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv(env_name, value)
    s = _build(fake_repo)
    assert s[flag] == expect


@pytest.mark.parametrize("env_name", [APT_ENV, PYPI_ENV, NPM_ENV])
def test_blank_mirror_env_counts_as_unset(fake_repo, monkeypatch, env_name):
    """与环境变量通行规则一致：空值视同未设，不报错。"""
    monkeypatch.setenv(env_name, "  ")
    s = _build(fake_repo)
    flag = {APT_ENV: "apt_mirror", PYPI_ENV: "pypi_mirror", NPM_ENV: "npm_registry"}[env_name]
    assert s[flag] is None


def test_bad_mirror_preserves_previous_package(fake_repo, monkeypatch):
    """边界：非法镜像源值必须在装配之前拒掉——上一个好包一个字节都不能变。"""
    for e in (APT_ENV, PYPI_ENV, NPM_ENV):
        monkeypatch.delenv(e, raising=False)
    out = _build(fake_repo, pypi_mirror="https://pypi.tuna.tsinghua.edu.cn/simple")["out"]
    before = (out / "backend" / "Dockerfile").read_bytes()
    with pytest.raises(bo.BuildError, match="PyPI"):
        _build(fake_repo, pypi_mirror="not-a-url")
    assert (out / "backend" / "Dockerfile").read_bytes() == before
    assert _leftovers(out) == []


def test_first_build_with_bad_mirror_creates_no_output_dir(fake_repo, monkeypatch):
    """边界：从未成功打过包时，坏镜像源不许留下空壳目录。"""
    for e in (APT_ENV, PYPI_ENV, NPM_ENV):
        monkeypatch.delenv(e, raising=False)
    with pytest.raises(bo.BuildError, match="apt"):
        # 带路径的 apt 值是真错（官方目录路径不能本身拼重），必须在建目录之前拒掉
        _build(fake_repo, apt_mirror="https://mirrors.aliyun.com/pypi/simple")
    assert not (fake_repo / "build-output").exists()


def test_summary_contains_mirror_keys(fake_repo, monkeypatch):
    """摘要必须含四个镜像键，便于打印与日志回溯。"""
    for e in (APT_ENV, PYPI_ENV, NPM_ENV):
        monkeypatch.delenv(e, raising=False)
    s = _build(fake_repo, apt_mirror="mirrors.aliyun.com",
               pypi_mirror="https://pypi.tuna.tsinghua.edu.cn/simple",
               npm_registry="https://registry.npmmirror.com")
    assert s["apt_mirror"] == "mirrors.aliyun.com"
    assert s["pypi_mirror"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert s["npm_registry"] == "https://registry.npmmirror.com"
    assert s["gateway_domain"] == bo.DEFAULT_GATEWAY_DOMAIN


# ---------------------------------------------------------------- 真 changelog canary
#
# 上面每个用例都在 tmp_path 的假仓库里跑：快、确定、不连库不碰 docker。代价是处置表与守卫
# 规则对着假数据永远是绿的——真 changelog 里的语句改了形、或者被整条删掉让登记条目变成
# 死条目，没有任何用例会发现，要等到出包那一刻才拒（或者更糟：不拒）。
# 下面两个 canary 直接对仓库里的真 changelog/ 合成一次。同样不连库、不碰 docker、不跑 npm。

def test_real_changelog_synthesizes_and_counts_match_reality():
    """canary：真 changelog/ 必须能合成通过，且 footer 报的来源文件数与目录里实际数量一致。"""
    files = sorted((bo.REPO_ROOT / "changelog").glob("V*.sql"))
    assert files, "仓库里没有 changelog/V*.sql，canary 失去了对象（判定域失效）"

    sql = bo.synth_init_sql(bo.REPO_ROOT / "changelog", [], ROOT, generated_at=STAMP)
    footer = [ln for ln in sql.splitlines() if "合成完成" in ln]
    assert footer, "产物里没有合成完成的 footer，运维就少了唯一的语义入口"
    assert f"结构段 {len(files)} 个来源文件" in footer[0], footer[0]


def test_real_changelog_dispositions_are_not_dead_entries():
    """canary：处置表每条都得还能在真 changelog 里找到，且 footer 的改写处数与之同源。

    「找不到」意味着条目已死——守卫对着一句不再存在的 SQL 空转。危险的不是它现在不生效，
    而是它让人以为这块已经处置过了；同一语义以新形状回来时，读表的人会先看表再看代码。
    footer 的处数则是运维理解产物语义的入口，必须与处置表算出来的一致，不能各说各话。
    """
    changelog = bo.REPO_ROOT / "changelog"
    raw = "\n".join(p.read_text(encoding="utf-8") for p in sorted(changelog.glob("V*.sql")))
    present = {bo._normalize_statement(s)
               for s in bo.split_statements(bo._strip_sql_comments(raw))}

    dead = [reason for unsafe, _safe, reason in bo._UPDATE_DISPOSITIONS if unsafe not in present]
    assert dead == [], (
        f"处置表里这些条目在真 changelog 里已经找不到了：{dead}。"
        "请确认是被有意删除还是改了形，并相应更新 _UPDATE_DISPOSITIONS（表外语句一律拒包）。")

    sql = bo.synth_init_sql(changelog, [], ROOT, generated_at=STAMP)
    footer = [ln for ln in sql.splitlines() if "合成完成" in ln][0]
    rewritten = int(re.search(r"裸 UPDATE 改写 (\d+) 处", footer).group(1))
    expected = sum(1 for unsafe, safe, _r in bo._UPDATE_DISPOSITIONS
                   if safe is not None and unsafe in present)
    assert rewritten == expected, f"footer 说改写 {rewritten} 处，处置表里该改的是 {expected} 处"

    # 判定「保留原句」的那条（WHERE 自限）必须逐字进产物：它被谁顺手「幂等化」一下，
    # 去重语义就变了，而这正是处置表登记它为 None 而不是改写的全部理由。
    active = " ".join(bo._strip_sql_comments(sql).split())
    for unsafe, safe, reason in bo._UPDATE_DISPOSITIONS:
        if safe is None:
            assert unsafe in active, f"自限语句（{reason}）没能逐字保留在产物里"



def test_requirements_declare_greenlet_for_the_async_engine():
    """回归：异步 SQLAlchemy 必须显式带 `asyncio` extra。

    SQLAlchemy 2.1 起 greenlet 不再是基础依赖，只在 `asyncio` extra 里。只写
    `sqlalchemy>=2.0.30` 时，干净的构建环境会解析到 2.1.x、greenlet 不进镜像，
    容器里 `create_async_engine` 一导入就 ImportError；本地因为旧版 2.0.x 顺带
    装了 greenlet，这个坑在开发机上完全看不出来。
    """
    req = (bo.REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^sqlalchemy\[[^\]]*\basyncio\b[^\]]*\]", req, re.MULTILINE), (
        "requirements.txt 里的 sqlalchemy 必须带 [asyncio] extra，否则新装环境拿不到 greenlet")
