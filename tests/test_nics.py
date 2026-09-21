# tests/test_nics.py
import json

import pytest
from app.deploy import nics
from app.db import make_engine, make_session_factory

# 模块级 pytestmark 已删：asyncio_mode=auto（pytest.ini）自动接管 async 用例，
# 而本文件的同步用例挂着该标记会各刷一条 PytestWarning（门禁输出的噪音来自 brief 原文，非新行为）。


def test_candidates_shape_and_filters(monkeypatch):
    monkeypatch.setattr(nics, "_via_hostname", lambda: ["10.1.2.3", "127.0.0.1", "169.254.9.9", "172.28.5.1"])
    monkeypatch.setattr(nics, "_via_udp", lambda: "10.1.2.3")
    cands = nics.lan_candidates()
    ips = [c["ip"] for c in cands]
    assert "10.1.2.3" in ips and "127.0.0.1" not in ips and "169.254.9.9" not in ips
    assert next(c for c in cands if c["ip"] == "10.1.2.3")["primary"] is True
    assert next(c for c in cands if c["ip"] == "172.28.5.1")["docker_hint"] is True


def test_update_readme_marker_block(tmp_path):
    (tmp_path / "README.md").write_text("# 应用\n\n正文\n", encoding="utf-8")
    nics.update_readme(tmp_path, "http://10.1.2.3:8210", "b20260920120000")
    t = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "<!-- zc:publish -->" in t and "http://10.1.2.3:8210" in t and t.count("# 应用") == 1
    nics.update_readme(tmp_path, "http://10.1.2.3:8210", "b20260920130000")   # 幂等刷新不堆叠
    t2 = (tmp_path / "README.md").read_text(encoding="utf-8")
    # brief 原文数的是 "<!-- zc:publish -->"，但闭合标记是 "<!-- /zc:publish -->"（多一个 "/"），
    # 不是前者的子串 → 一块也是 1，`== 2` 与 brief 给的实现互斥（已实跑证实）。作者要钉的语义是
    # 「标记对只有一组、刷新不堆叠」，故按标签名数：一块 = 2（起+止），堆叠 = 4。
    assert t2.count("zc:publish") == 2 and "b20260920130000" in t2 and "b20260920120000" not in t2


def test_update_readme_keeps_line_endings(tmp_path):
    """I-1：README 全程字节保真 —— 不得把 CRLF 文件整份翻成 LF（探针：写前 3 个 \\r\\n，刷新后 0）。

    两侧行尾都用 write_bytes 手工构造：Path.write_text 不传 newline 时走通用换行，
    在 Windows 上会把 "\\n" 翻成 "\\r\\n"，用它造 LF 用例实际落盘的是 CRLF 文件，钉不住 LF 那一侧。
    """
    def run(dirname, src, eol):
        d = tmp_path / dirname
        d.mkdir()
        (d / "README.md").write_bytes(src.encode("utf-8"))
        nics.update_readme(d, "http://10.1.2.3:8210", "b20260920120000")
        raw = (d / "README.md").read_bytes().decode("utf-8")
        assert src in raw                                    # 未动的正文逐字节还在（整份翻转会先在这红）
        assert raw.count(eol) == src.count(eol) + 4          # 原行数 + 块 3 行 + 前插分隔 1 行：只增不减
        assert raw.count("\n") == raw.count(eol)             # 不存在与主导行尾不一致的裸 LF
        nics.update_readme(d, "http://10.1.2.3:8210", "b20260920130000")   # 幂等刷新
        raw2 = (d / "README.md").read_bytes().decode("utf-8")
        assert src in raw2 and raw2.count("zc:publish") == 2               # 不堆叠、正文仍未被改写
        assert "b20260920130000" in raw2 and "b20260920120000" not in raw2
        assert raw2.count("\n") == raw2.count(eol)

    run("crlf", "# 应用\r\n\r\n正文\r\n", "\r\n")
    run("lf", "# 应用\n\n正文\n", "\n")


# 两个哨兵都不能用 None 顶替：prev is None 既可能是「键在库里存的是 JSON null」，
# 也可能是「库里根本没这行」，还可能是「SELECT 自己抛了、还没读到」——三种语义必须分开。
_UNSET = object()      # 没读到（含 SELECT 前就炸）：我们一个字都没写，什么都不该还原
_NO_ROW = object()     # 读到了、但库里没有这行：用例的 INSERT 造出了它，收尾该删掉自己造的行


async def test_pick_and_urls(monkeypatch):
    engine = make_engine()
    sf = make_session_factory(engine)
    monkeypatch.setattr(nics, "_via_hostname", lambda: ["192.168.1.50"])
    monkeypatch.setattr(nics, "_via_udp", lambda: "192.168.1.50")
    from sqlalchemy import text as t
    prev = _UNSET
    try:
        async with sf() as s:
            # 进入时先记录 deploy.default_nic 的旧值（本用例连的是 make_engine() 的平台默认库 =
            # 共享 dev 库，用户可能真在配置页选过网卡）。收尾写回的是**读到的旧值**，不是种子 ""：
            # 「自己的产物自己带走」= 不留示例 IP，也不抹别人的选择（T5 审查 I-2；
            # 旧写法无条件 UPDATE value='""'，实测跑一次 pytest 就把用户的网卡选择静默清空）。
            row = (await s.execute(t("SELECT value FROM app_config WHERE key = 'deploy.default_nic'"))).first()
            prev = row[0] if row else _NO_ROW
            await s.execute(t("INSERT INTO app_config (key, value) VALUES ('deploy.default_nic', '\"192.168.1.50\"') "
                              "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"))
            await s.commit()
        assert nics.pick_lan_ip.__wrapped__ if hasattr(nics.pick_lan_ip, "__wrapped__") else True
        urls = await nics.app_urls(sf, {"ports": {"prod": 8210}, "prod_port": 8210})
        assert urls["localhost"] == "http://localhost:8210"
        assert urls["lan"] == "http://192.168.1.50:8210"
        assert any(c["ip"] == "192.168.1.50" for c in urls["lanCandidates"])
    finally:
        # prev 仍是 _UNSET 说明连 SELECT 都没跑到（或它自己抛了），我们一个字都没写，不该还原更不该删。
        if prev is not _UNSET:
            async with sf() as s:
                if prev is _NO_ROW:
                    await s.execute(t("DELETE FROM app_config WHERE key = 'deploy.default_nic'"))
                else:
                    # value 是 JSONB，asyncpg 读出来已解码成 Python 对象（'192.168.1.50' 是 str 不是 JSON 文本），
                    # 直接绑回原始串会被 PG 当非法 JSON；按 config_service 的同款手法 json.dumps + CAST(:v AS JSONB)。
                    await s.execute(t("UPDATE app_config SET value = CAST(:old AS JSONB) WHERE key = 'deploy.default_nic'"),
                                    {"old": json.dumps(prev)})
                await s.commit()
        await engine.dispose()   # 同仓 test_apps_api / test_ws 惯例：自造的池自放


def test_urls_and_nics_routes_registered():
    """端点接线（app/api/system.py + apps.py + main.py include）的最小说证。

    不读 `app.routes`：本版本 FastAPI 把 include_router 挂成惰性的 `_IncludedRouter`（没有 .path），
    openapi 的 paths 才是拍平后的事实面。构造 app 不跑 lifespan，零 DB / 零 Docker 依赖。
    """
    from app.main import create_app
    paths = set(create_app().openapi()["paths"])
    assert "/api/system/nics" in paths and "/api/apps/{app_id}/urls" in paths
