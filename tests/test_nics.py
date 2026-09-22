# tests/test_nics.py
import pytest
from app.deploy import nics

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


async def test_app_urls_domain():
    """prod 访问地址统一为网关域名 https://{slug}.zerocode.com（通配证书只覆盖一级 slug）。

    slug 缺位/空白回空 domain——不谎拼 https://.zerocode.com（网关解不到、徒增误导）。
    app_urls 不碰库（纯 slug 投影），sf 传 None 即可。
    """
    assert await nics.app_urls(None, {"slug": "demoapp"}) == {"domain": f"https://demoapp.{nics.GATEWAY_DOMAIN}"}
    assert await nics.app_urls(None, {"slug": "  spaced  "}) == {"domain": f"https://spaced.{nics.GATEWAY_DOMAIN}"}
    assert await nics.app_urls(None, {}) == {"domain": ""}


def test_urls_and_nics_routes_registered():
    """端点接线（app/api/system.py + apps.py + main.py include）的最小说证。

    不读 `app.routes`：本版本 FastAPI 把 include_router 挂成惰性的 `_IncludedRouter`（没有 .path），
    openapi 的 paths 才是拍平后的事实面。构造 app 不跑 lifespan，零 DB / 零 Docker 依赖。
    """
    from app.main import create_app
    paths = set(create_app().openapi()["paths"])
    assert "/api/system/nics" in paths and "/api/apps/{app_id}/urls" in paths
