# tests/test_nics.py
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


async def test_pick_and_urls(monkeypatch):
    sf = make_session_factory(make_engine())
    monkeypatch.setattr(nics, "_via_hostname", lambda: ["192.168.1.50"])
    monkeypatch.setattr(nics, "_via_udp", lambda: "192.168.1.50")
    from sqlalchemy import text as t
    async with sf() as s:
        await s.execute(t("INSERT INTO app_config (key, value) VALUES ('deploy.default_nic', '\"192.168.1.50\"') "
                          "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"))
        await s.commit()
    assert nics.pick_lan_ip.__wrapped__ if hasattr(nics.pick_lan_ip, "__wrapped__") else True
    try:
        urls = await nics.app_urls(sf, {"ports": {"prod": 8210}, "prod_port": 8210})
        assert urls["localhost"] == "http://localhost:8210"
        assert urls["lan"] == "http://192.168.1.50:8210"
        assert any(c["ip"] == "192.168.1.50" for c in urls["lanCandidates"])
    finally:
        # brief 用例走的是 make_engine()（无 URL = 平台默认库，即共享 dev 库），收尾把种子值还原：
        # 本波纪律是「自己的产物自己带走」，不把示例 IP 留在用户的配置页上（断言失败也还原）。
        async with sf() as s:
            await s.execute(t("UPDATE app_config SET value = '\"\"' WHERE key = 'deploy.default_nic'"))
            await s.commit()


def test_urls_and_nics_routes_registered():
    """端点接线（app/api/system.py + apps.py + main.py include）的最小说证。

    不读 `app.routes`：本版本 FastAPI 把 include_router 挂成惰性的 `_IncludedRouter`（没有 .path），
    openapi 的 paths 才是拍平后的事实面。构造 app 不跑 lifespan，零 DB / 零 Docker 依赖。
    """
    from app.main import create_app
    paths = set(create_app().openapi()["paths"])
    assert "/api/system/nics" in paths and "/api/apps/{app_id}/urls" in paths
