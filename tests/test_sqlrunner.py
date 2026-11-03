# tests/test_sqlrunner.py
import re
import shutil
import pytest
from pathlib import Path
from app.db import make_engine, make_session_factory
from app.deploy import provision, sqlrun

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not sqlrun.docker_available(), reason="docker 不可用")]

SLUG = "zctask9"
ROOT = Path("D:/__zc_task9__")            # 试验目录根（仓库外，docker bind mount 用）
SQL_DIR = ROOT / SLUG / "sqlchange"
RUN_SH = Path(sqlrun.__file__).resolve().parent / "sqlrunner" / "run.sh"

GOOD1 = """-- V20260918_01_init.sql
-- 用途：t1 表  影响：t1
CREATE TABLE IF NOT EXISTS t1 (id INT);
"""
GOOD2 = """-- V20260918_02_add.sql
-- 用途：t1 加列  影响：t1
ALTER TABLE t1 ADD COLUMN IF NOT EXISTS name VARCHAR(50);
"""
BAD = """-- V20260918_03_bad.sql
-- 用途：故意失败  影响：t1
THIS IS NOT SQL;
"""

def _write(files):
    # brief 只清 SQL_DIR.parent（= ROOT/SLUG），外层根 ROOT 由 mkdir(parents=True) 建出来后没人收，
    # 这里整棵清掉，跑完不留空壳目录。
    shutil.rmtree(ROOT, ignore_errors=True)
    SQL_DIR.mkdir(parents=True)
    for name, text in files.items():
        (SQL_DIR / name).write_text(text, encoding="utf-8", newline="")

@pytest.fixture(scope="module", autouse=True)
async def _image():
    await sqlrun.build_if_missing()

# fixture 名即参数名：测试用 cred 形参接收本环境凭据（计划已修正 fixture 链缺陷，不用全局 _cred_store）
@pytest.fixture(autouse=True)
async def cred():
    sf = make_session_factory(make_engine())
    await provision.drop_env(sf, SLUG, "dev")
    c = await provision.provision_env(sf, SLUG, "dev")
    yield c
    await provision.drop_env(sf, SLUG, "dev")
    shutil.rmtree(ROOT, ignore_errors=True)

async def _apply(cred):
    return await sqlrun.run_sqlchange(SQL_DIR.parent, "dev", cred)

async def test_apply_skip_and_history(cred):
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_02_add.sql": GOOD2})
    r = await _apply(cred)
    assert r["ok"] is True and "OK V20260918_01_init.sql" in r["log"] and "OK V20260918_02_add.sql" in r["log"]
    r2 = await _apply(cred)
    assert r2["ok"] is True and "SKIP" in r2["log"] and "OK V20260918_01" not in r2["log"]
    # 历史表可查
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
    async with eng.connect() as c:
        rows = (await c.execute(text("SELECT filename, success FROM sqlchange_history ORDER BY filename"))).fetchall()
    await eng.dispose()
    assert [tuple(r) for r in rows] == [("V20260918_01_init.sql", True), ("V20260918_02_add.sql", True)]

async def test_failure_exit2_and_reapply_after_fix(cred):
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_03_bad.sql": BAD})
    r = await _apply(cred)
    assert r["ok"] is False and r["code"] == 2 and "FAIL V20260918_03_bad.sql" in r["log"]
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_03_bad.sql": GOOD2.replace("02_add", "03_bad").replace("-- V20260918_02_add.sql", "-- V20260918_03_bad.sql")})
    r2 = await _apply(cred)
    assert r2["ok"] is True  # 失败文件修复后补跑成功，01 不重跑
    # 补跑成功后历史行必须记上耗时（规格 07 §4.3「成功 → INSERT history(success=true, duration)」）：
    # 成功分支的 ON CONFLICT DO UPDATE 若不写 duration_ms，重跑成功的行会永久留着失败时的 NULL。
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
    async with eng.connect() as c:
        rows = (await c.execute(text(
            "SELECT filename, success, duration_ms FROM sqlchange_history ORDER BY filename"))).fetchall()
    await eng.dispose()
    assert [r[0] for r in rows] == ["V20260918_01_init.sql", "V20260918_03_bad.sql"]
    assert rows[1][1] is True and rows[1][2] is not None  # 重跑成功的 03：success=true 且 duration 已补齐

async def test_tamper_exit3(cred):
    _write({"V20260918_01_init.sql": GOOD1})
    assert (await _apply(cred))["ok"] is True
    _write({"V20260918_01_init.sql": GOOD1 + "\n-- 改过一行\nSELECT 1;\n"})
    r = await _apply(cred)
    assert r["ok"] is False and r["code"] == 3 and "TAMPER" in r["log"]

async def test_empty_dir_ok(cred):
    _write({})
    r = await _apply(cred)
    assert r["ok"] is True


# ---- 收尾修复（审查报告 I-1 / N-3）----

async def test_missing_sqlchange_dir_fails_closed(cred, monkeypatch):
    """I-1：sqlchange/ 缺失时 Docker 会补空挂载 → 绿色空跑击穿 migrate 闸门，宿主侧必须 fail-closed。

    「未启动任何容器」用 _docker/ensure_zcnet 模块级 seam 断言（比 docker ps -a 计数更强的轻量方式：
    run 用了 --rm，容器退出即消失，计数恒 0 属真空断言；seam 断言则钉死「一条 docker 命令都不下发」）。
    """
    calls: list[tuple] = []

    async def _no_docker(*args, **kw):
        calls.append(tuple(args))
        return 0, ""

    async def _no_net():
        calls.append(("ensure_zcnet",))

    monkeypatch.setattr(sqlrun, "_docker", _no_docker)
    monkeypatch.setattr(sqlrun, "ensure_zcnet", _no_net)
    shutil.rmtree(ROOT, ignore_errors=True)
    (ROOT / SLUG).mkdir(parents=True)  # app_dir 在，sqlchange/ 不在
    r = await sqlrun.run_sqlchange(ROOT / SLUG, "dev", cred)
    assert r["ok"] is False and r["code"] == 2
    assert "目录不存在" in r["log"]
    assert calls == []  # 守卫在起容器之前，docker 面零接触


async def test_run_sqlchange_self_heals_image(cred, monkeypatch):
    """M-1（终审 F-01）：镜像只在平台启动构建一次，Docker 晚启动即永久缺失 → 预览永久 error。

    main.py 注释承诺「首次预览时会重试」：run_sqlchange 必须在 zcnet 自愈旁补镜像自愈，
    且必须先于起容器完成（否则 docker run 仍报 No such image）。build_if_missing 本已幂等
    （image inspect 命中即 return），seam 断言方式同 I-1 用例：不真删镜像，只钉调用路径。
    """
    calls: list[str] = []

    async def _spy_build():
        calls.append("build_if_missing")

    async def _spy_net():
        calls.append("ensure_zcnet")

    async def _no_docker(*args, **kw):
        calls.append("docker run")
        return 0, ""

    monkeypatch.setattr(sqlrun, "build_if_missing", _spy_build)
    monkeypatch.setattr(sqlrun, "ensure_zcnet", _spy_net)
    monkeypatch.setattr(sqlrun, "_docker", _no_docker)
    _write({"V20260918_01_init.sql": GOOD1})
    r = await sqlrun.run_sqlchange(SQL_DIR.parent, "dev", cred)
    assert r["ok"] is True
    assert calls == ["ensure_zcnet", "build_if_missing", "docker run"]


async def test_run_sh_record_failure_guard():
    """N-3：成功分支 history UPSERT 的返回码不得丢弃——记账失败宁可闸门红（exit 2），不绿色假成功。

    集成层注入「连得上但写不进 history」需打挂表权限且与迁移同库同角色（owner 必可写），
    稳定构造成本过高，故退为脚本层静态断言（审查报告已授权该取舍）。
    """
    src = RUN_SH.read_text(encoding="utf-8")
    assert re.search(
        r'if ! \$PSQL -q -c "INSERT INTO sqlchange_history.*?FAIL RECORD \$rel.*?exit 2',
        src, re.S), "成功分支 UPSERT 未做返回码守卫（FAIL RECORD + exit 2）"
    # OK 行必须在守卫之后：防「先 OK 后 exit 2」的自相矛盾回退
    assert src.index('echo "OK $rel"') > src.index("FAIL RECORD")


# ---- 数据轨三段（ADR-0039：公共段 + dev 段 + prod 段）----

SHARED = """-- V20261101_01_shared.sql
-- 用途：公共段建表并落一行  影响：t_seg
CREATE TABLE IF NOT EXISTS t_seg (who VARCHAR(20));
INSERT INTO t_seg VALUES ('shared');
"""
DEV_ONLY = """-- V20261101_02_demo.sql
-- 用途：dev 段演示行（与 prod 段同名不同内容）  影响：t_seg
INSERT INTO t_seg VALUES ('dev');
"""
PROD_ONLY = """-- V20261101_02_demo.sql
-- 用途：prod 段上线行  影响：t_seg
INSERT INTO t_seg VALUES ('prod');
"""


def _write_segments():
    """公共段 1 个 + dev 段/prod 段各 1 个**同名**文件（内容不同）。"""
    _write({"V20261101_01_shared.sql": SHARED})
    for seg, text in (("dev", DEV_ONLY), ("prod", PROD_ONLY)):
        d = SQL_DIR / seg
        d.mkdir()
        (d / "V20261101_02_demo.sql").write_text(text, encoding="utf-8", newline="")


async def _query(cred, sql):
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
    try:
        async with eng.connect() as c:
            return [tuple(r) for r in (await c.execute(_text(sql))).fetchall()]
    finally:
        await eng.dispose()


async def test_dev_runs_shared_plus_dev_segment(cred):
    """dev 库只跑「公共段 + dev 段」，prod 段的文件对它永不可见。"""
    _write_segments()
    r = await sqlrun.run_sqlchange(SQL_DIR.parent, "dev", cred)
    assert r["ok"] is True, r["log"]
    assert "OK V20261101_01_shared.sql" in r["log"]
    assert "OK dev/V20261101_02_demo.sql" in r["log"]
    assert "prod" not in r["log"]
    assert {row[0] for row in await _query(cred, "SELECT who FROM t_seg")} == {"shared", "dev"}


async def test_history_keys_are_relative_and_same_base_name_does_not_collide(cred):
    """两段同名文件的 basename 相同：记账必须是**相对 sqlchange/ 的路径**，否则同一库里撞主键、
    还会把另一段的文件误判成篡改。"""
    _write_segments()
    assert (await sqlrun.run_sqlchange(SQL_DIR.parent, "dev", cred))["ok"]
    r = await sqlrun.run_sqlchange(SQL_DIR.parent, "prod", cred)
    assert r["ok"] is True, r["log"]
    # 公共段已记账 → SKIP；prod 段是新增 → OK（与 dev 段同 basename 也不互撞）
    assert "SKIP V20261101_01_shared.sql" in r["log"]
    assert "OK prod/V20261101_02_demo.sql" in r["log"]
    rows = await _query(cred, "SELECT filename FROM sqlchange_history")
    # 三条独立记账（不设顺序断言：ORDER BY 走库的 collation，非 ASCII 序，与执行顺序无关）
    assert {row[0] for row in rows} == {
        "V20261101_01_shared.sql", "dev/V20261101_02_demo.sql", "prod/V20261101_02_demo.sql"}
    assert {row[0] for row in await _query(cred, "SELECT who FROM t_seg")} == {
        "shared", "dev", "prod"}


async def test_anchor_query_order_matches_execution_order(cred):
    """anchor 取数顺序必须等于 run.sh 的实际执行顺序——商店前缀约束 L 的前提。

    平台 PG 实例是 en_US.utf8 collation：默认 `ORDER BY filename` 把 `prod/…` 排到 `V…` 之前，
    run.sh 却按字节序先跑公共段。三段落地后这个分叉会真咬人——追加一个顶层 D 文件就能让
    「上一版序列是本版前缀」判定假红、上架被拒（ADR-0039）。
    """
    from app.store import anchor as anchor_mod
    _write_segments()
    r = await sqlrun.run_sqlchange(SQL_DIR.parent, "prod", cred)
    assert r["ok"] is True, r["log"]
    executed = [ln.split("OK ", 1)[1].strip() for ln in r["log"].splitlines()
                if ln.startswith("OK ")]
    assert executed == ["V20261101_01_shared.sql", "prod/V20261101_02_demo.sql"]
    assert [row[0] for row in await _query(cred, anchor_mod.HISTORY_SQL)] == executed


async def test_segment_tamper_is_detected_per_path(cred):
    """篡改检测按段路径独立：改 prod 段文件只让 prod 那条红，dev 段仍算已应用。"""
    _write_segments()
    assert (await sqlrun.run_sqlchange(SQL_DIR.parent, "dev", cred))["ok"]
    assert (await sqlrun.run_sqlchange(SQL_DIR.parent, "prod", cred))["ok"]
    (SQL_DIR / "prod" / "V20261101_02_demo.sql").write_text(
        PROD_ONLY + "\nSELECT 1;\n", encoding="utf-8", newline="")
    r = await sqlrun.run_sqlchange(SQL_DIR.parent, "prod", cred)
    assert r["ok"] is False and r["code"] == 3
    assert "TAMPER prod/V20261101_02_demo.sql" in r["log"]


async def test_invalid_env_fails_closed_before_docker(cred, monkeypatch):
    """env 不是 dev/prod 时在起容器之前拒：容器侧拿到野值会拼出没人认领的挂载路径。"""
    calls: list[tuple] = []

    async def _no_docker(*args, **kw):
        calls.append(tuple(args))
        return 0, ""

    async def _no_net():
        calls.append(("ensure_zcnet",))

    monkeypatch.setattr(sqlrun, "_docker", _no_docker)
    monkeypatch.setattr(sqlrun, "ensure_zcnet", _no_net)
    _write_segments()
    with pytest.raises(ValueError):
        await sqlrun.run_sqlchange(SQL_DIR.parent, "verify", cred)
    assert calls == []


async def test_run_sh_requires_and_selects_env_segment():
    """run.sh 侧的守卫：目标环境必填并白名单校验，且必须把本环境段并进同一次循环。"""
    src = RUN_SH.read_text(encoding="utf-8")
    assert re.search(r'TARGET_ENV="\$\{TARGET_ENV:?', src), \
        "TARGET_ENV 未做必填（缺值必须让容器红，不许静默只跑公共段）"
    assert re.search(r'case "\$TARGET_ENV" in dev\|prod', src), "TARGET_ENV 未做 dev|prod 白名单"
    assert re.search(r'/sql/\$TARGET_ENV/\*\.sql', src), "循环没把本环境段的文件并进来"
