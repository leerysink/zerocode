# tests/test_skills_api.py
"""Ticket 001/003 技能 HTTP 面：install→list→PATCH→DELETE 闭环、frontmatter 校验、
slug 后缀、双账号隔离与越权 404、母本磁盘治理。最高接缝＝HTTP API（installer 远端用
httpx.MockTransport 注入）。
"""
import asyncio
import json
import re

import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _skills_data_dir(tmp_path, monkeypatch):
    """技能母本落 tmp，绝不写仓库 data/。data_dir() 每次现读 env，故作用域内生效。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


SKILL_MD = (b"---\n"
            b"name: PDF Processing\n"
            b"description: Handle PDF files end to end\n"
            b"---\n"
            b"# PDF\nSee [guide](references/guide.md).\n")


def _transport():
    import httpx

    def handler(request):
        if request.url.path.upper().endswith("SKILL.MD"):
            return httpx.Response(200, content=SKILL_MD)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_install_then_list_and_disk(client):
    client.app.state.skills_http_transport = _transport()
    r = await client.post("/api/skills/install",
                          json={"url": "https://raw.invalid/o/r/master/pdf/SKILL.md"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "PDF Processing"
    assert sk["slug"] == "pdf-processing"
    assert sk["enabled"] is True
    assert sk["source_type"] == "url"
    # 列表可见
    rows = (await client.get("/api/skills")).json()
    assert [x["id"] for x in rows] == [sk["id"]]
    # 磁盘母本内容与来源逐字节一致
    assert (Path(sk["skill_dir"]) / "SKILL.md").read_bytes() == SKILL_MD


async def test_missing_description_rejected_with_fieldname(client):
    import httpx

    bad = b"---\nname: OnlyName\n---\nbody"

    async def handler(request):
        return httpx.Response(200, content=bad)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert "description" in r.text
    # 无半成品：列表仍空
    assert (await client.get("/api/skills")).json() == []


async def test_same_name_reinstall_gets_slug_suffix(client):
    client.app.state.skills_http_transport = _transport()
    r1 = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    r2 = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    s1, s2 = r1.json(), r2.json()
    assert s1["slug"] == "pdf-processing"
    assert s2["slug"] == "pdf-processing-2"
    assert s1["id"] != s2["id"]
    # 两个母本目录并存且不同
    assert s1["skill_dir"] != s2["skill_dir"]
    assert Path(s2["skill_dir"], "SKILL.md").is_file()


async def test_github_repo_url_scans_via_github_channel(client):
    """002 后 GitHub 仓库 URL 不再占位报错：扫到多命中返候选（详表见 github 测试文件）。"""
    import httpx

    async def handler(request):
        if request.url.path == "/repos/owner/repo":
            return httpx.Response(200, json={"default_branch": "main"})
        tree = [{"path": "skills/a/SKILL.md", "type": "blob", "size": 10},
                {"path": "skills/b/SKILL.md", "type": "blob", "size": 10}]
        return httpx.Response(200, json={"tree": tree, "truncated": False})
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 200
    assert [c["path"] for c in r.json()["candidates"]] == ["skills/a", "skills/b"]


async def test_direct_url_must_point_to_skill_md(client):
    client.app.state.skills_http_transport = _transport()
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/readme.md"})
    assert r.status_code == 400


async def test_two_accounts_list_isolated(two_auth_clients):
    alice, bob = two_auth_clients
    alice.app.state.skills_http_transport = _transport()
    r = await alice.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201
    assert [x["id"] for x in (await alice.get("/api/skills")).json()] == [r.json()["id"]]
    # bob 完全看不到 alice 的技能
    assert (await bob.get("/api/skills")).json() == []


async def test_requires_login(anon_client):
    assert (await anon_client.get("/api/skills")).status_code == 401


# ---- 审查修复回归：SSRF/长度/网络异常/限长/不安全引用 ----

async def test_intranet_or_nonhttp_url_rejected(client):
    import httpx

    async def handler(request):
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    for bad in ("http://127.0.0.1/a/SKILL.md",
                "http://169.254.169.254/latest/meta/SKILL.md",
                "http://10.0.0.8/x/SKILL.md",
                "http://localhost:8080/a/SKILL.md",
                "file:///tmp/a/SKILL.md"):
        r = await client.post("/api/skills/install", json={"url": bad})
        assert r.status_code == 400, bad
    assert (await client.get("/api/skills")).json() == []


async def test_overlong_url_rejected(client):
    r = await client.post("/api/skills/install",
                          json={"url": "https://x.invalid/" + "a" * 1200 + "/SKILL.md"})
    assert r.status_code == 400


async def test_overlong_name_rejected(client):
    import httpx

    bad = ("---\nname: " + "N" * 300 + "\ndescription: d\n---\n").encode()

    async def handler(request):
        return httpx.Response(200, content=bad)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert (await client.get("/api/skills")).json() == []


async def test_oversize_file_rejected(client):
    import httpx

    huge = SKILL_MD + b"x" * (1024 * 1024 + 10)

    async def handler(request):
        return httpx.Response(200, content=huge)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert "大小上限" in r.text
    assert (await client.get("/api/skills")).json() == []


async def test_network_error_maps_to_400(client):
    import httpx

    async def handler(request):
        raise httpx.ConnectError("boom", request=request)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400  # 不能是 500


async def test_unsafe_relative_refs_skipped(client):
    import httpx

    md = (b"---\nname: RefSkill\ndescription: d\n---\n"
          b"[ok](references/guide.md) [evil](../evil.md) [up](./b.md)\n")

    async def handler(request):
        if request.url.path.upper().endswith("SKILL.MD"):
            return httpx.Response(200, content=md)
        return httpx.Response(200, content=b"ref body")
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text  # 非法引用不拖死整次安装
    skill_dir = Path(r.json()["skill_dir"])
    assert (skill_dir / "references" / "guide.md").is_file()
    assert (skill_dir / "b.md").is_file()
    assert not (skill_dir.parent / "evil.md").exists()


async def test_redirect_to_internal_host_rejected(client):
    """审查 I-1：公网 URL 借 302 跳内网必须被拦，且内网请求根本不发出（002 逐跳预检）。"""
    import httpx

    seen = []

    async def handler(request):
        seen.append(request.url.host)
        if request.url.host == "outer.invalid":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:5433/x/SKILL.md"})
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://outer.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert "内网" in r.text
    assert "127.0.0.1" not in seen  # 落点预检在请求前，不是事后拦读
    assert (await client.get("/api/skills")).json() == []


async def test_public_redirect_chain_followed(client):
    """公网→公网的合法重定向仍能装成（逐跳手动跟不再误伤）。"""
    import httpx

    async def handler(request):
        if request.url.host == "short.invalid":
            return httpx.Response(301, headers={"location": "https://long.invalid/a/SKILL.md"})
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://short.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text


async def test_obfuscated_ip_host_rejected(client):
    """审查 M-1：inet_aton 缩写/十六进制形态的回环地址不得当域名放行。"""
    import httpx

    async def handler(request):
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    for bad in ("http://2130706433/a/SKILL.md",
                "http://127.1/a/SKILL.md",
                "http://0x7f000001/a/SKILL.md",
                "http://127.1./a/SKILL.md",
                "http://169.254.169.254./x/SKILL.md"):
        r = await client.post("/api/skills/install", json={"url": bad})
        assert r.status_code == 400, bad
    assert (await client.get("/api/skills")).json() == []


async def test_oversize_optional_ref_skipped_not_fatal(client):
    """审查 M-2：可选引用文件超限降级为跳过，本体照装。"""
    import httpx

    md = (b"---\nname: BigRef\ndescription: d\n---\n"
          b"[ok](references/guide.md) [big](big.md)\n")

    async def handler(request):
        if request.url.path.upper().endswith("SKILL.MD"):
            return httpx.Response(200, content=md)
        if request.url.path.endswith("big.md"):
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        return httpx.Response(200, content=b"small ref")
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text
    skill_dir = Path(r.json()["skill_dir"])
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "references" / "guide.md").is_file()
    assert not (skill_dir / "big.md").exists()


async def test_direct_channel_ref_budget_stops_early(client):
    """复审 F-2：直链引用累积途中即拒，请求数不由远端链接条数线性放大。"""
    import httpx

    # 200 个相对引用，每个回 900KB（均在 1MB 单文件限内），合计远超 10MB
    links = " ".join(f"[r{i}](r{i}.md)" for i in range(200)).encode()
    md = b"---\nname: ChainRef\ndescription: d\n---\n" + links
    seen = []

    async def handler(request):
        seen.append(request.url.path)
        if request.url.path.upper().endswith("SKILL.MD"):
            return httpx.Response(200, content=md)
        return httpx.Response(200, content=b"z" * (900 * 1024))
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert "总大小" in r.text
    # 越预算即停：实际发出的请求远少于 200 个引用（约 12~13 个就触顶）
    assert len(seen) < 20, f"requests not stopped early: {len(seen)}"
    assert (await client.get("/api/skills")).json() == []


async def test_db_failure_cleans_up_skill_dir(client, monkeypatch, tmp_path):
    """审查 Blocker #1 兜底半边：DB 写入非 IntegrityError 异常不得留永久孤儿母本。"""
    from sqlalchemy.exc import DBAPIError

    import app.api.skills as skills_api
    client.app.state.skills_http_transport = _transport()
    # 把 RETURNING 列换成不存在的列，让 INSERT 在 DB 端炸出非 IntegrityError 异常
    monkeypatch.setattr(skills_api, "_COLUMNS", "id, no_such_column")
    with pytest.raises(DBAPIError):
        await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    # 母本目录已被清理：users/*/skills 下不留任何子目录/文件
    assert list((tmp_path / "data").glob("users/*/skills/*")) == []


# ---- Ticket 003：启用开关与删除（PATCH/DELETE + 越权 404）----


async def _install_one(client):
    client.app.state.skills_http_transport = _transport()
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_patch_flips_enabled(client):
    sk = await _install_one(client)
    assert sk["enabled"] is True
    r = await client.patch(f"/api/skills/{sk['id']}", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    rows = (await client.get("/api/skills")).json()
    assert [x["enabled"] for x in rows] == [False]  # 列表持久化看到停用
    r = await client.patch(f"/api/skills/{sk['id']}", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True


async def test_patch_nonexistent_404(client):
    r = await client.patch("/api/skills/99999999", json={"enabled": False})
    assert r.status_code == 404


async def test_delete_removes_row_and_disk(client, tmp_path):
    sk = await _install_one(client)
    skill_dir = Path(sk["skill_dir"])
    assert skill_dir.is_dir()
    r = await client.delete(f"/api/skills/{sk['id']}")
    assert r.status_code == 200, r.text
    assert (await client.get("/api/skills")).json() == []
    assert not skill_dir.exists()  # 母本同删，不留孤儿目录
    # 再删同一 id → 404（行已不在）
    assert (await client.delete(f"/api/skills/{sk['id']}")).status_code == 404


async def test_delete_removes_only_target_skill_dir(client, tmp_path):
    """删目标不误伤兄弟母本：并存两技能，删一个只清它那目录，另一个目录与行完好（复审 M-3）。"""
    a = await _install_one(client)
    b = await _install_one(client)
    assert a["skill_dir"] != b["skill_dir"]
    da, db = Path(a["skill_dir"]), Path(b["skill_dir"])
    assert da.is_dir() and db.is_dir()

    r = await client.delete(f"/api/skills/{a['id']}")
    assert r.status_code == 200, r.text
    assert not da.exists()  # 目标母本删除
    assert db.is_dir() and (db / "SKILL.md").is_file()  # 兄弟母本未受波及
    rows = (await client.get("/api/skills")).json()
    assert [x["id"] for x in rows] == [b["id"]]  # 只删了目标行


async def test_cross_account_patch_and_delete_404(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    sk = await _install_one(alice)
    assert (await bob.patch(f"/api/skills/{sk['id']}", json={"enabled": False})).status_code == 404
    assert (await bob.delete(f"/api/skills/{sk['id']}")).status_code == 404
    # 越权尝试后 alice 的行与母本分毫未动
    rows = (await alice.get("/api/skills")).json()
    assert [x["id"] for x in rows] == [sk["id"]] and rows[0]["enabled"] is True
    assert Path(sk["skill_dir"]).is_dir()


async def test_delete_dir_failure_keeps_row(client, monkeypatch):
    """故事8 的次序保险丝：目录删不掉则整体失败不动行，不造清单外的孤儿反例。"""
    import app.api.skills as skills_api
    from app.skills.library import SkillError

    sk = await _install_one(client)

    def _boom(uid, d):
        raise SkillError("模拟磁盘错误")
    monkeypatch.setattr(skills_api, "delete_skill_dir", _boom)
    r = await client.delete(f"/api/skills/{sk['id']}")
    assert r.status_code == 400
    assert "磁盘" in r.text
    rows = (await client.get("/api/skills")).json()
    assert [x["id"] for x in rows] == [sk["id"]]  # 行未动，可重试


async def test_delete_out_of_root_skill_dir_refused(client, tmp_path):
    """路径围栏：残行/改库把 skill_dir 指向根外时，DELETE 拒绝且不动那目录。"""
    sk = await _install_one(client)
    victim = tmp_path / "data" / "users" / "other"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "keep.txt").write_bytes(b"do not delete")

    async with client.app.state.session_factory() as s:
        from sqlalchemy import text
        await s.execute(text("UPDATE user_skill SET skill_dir=:d WHERE id=:i"),
                        {"d": str(victim), "i": sk["id"]})
        await s.commit()
    r = await client.delete(f"/api/skills/{sk['id']}")
    assert r.status_code == 400
    assert "路径异常" in r.text
    assert victim.is_dir() and (victim / "keep.txt").is_file()
    # 围栏内但已不存在的目录：再指回自己根下的空位，删除可自愈（行仍在）
    ghost = str(Path(sk["skill_dir"]).parent / "ghost-skill")
    async with client.app.state.session_factory() as s:
        from sqlalchemy import text
        await s.execute(text("UPDATE user_skill SET skill_dir=:d WHERE id=:i"),
                        {"d": ghost, "i": sk["id"]})
        await s.commit()
    r = await client.delete(f"/api/skills/{sk['id']}")
    assert r.status_code == 200  # 不存在视为已删，残行可清理
    assert (await client.get("/api/skills")).json() == []


# ---- Ticket 006：跨票全链路冒烟（API 安装 → agent 注入 → 受控读面 → 停用即隐 → 删除清盘）----

class _Rec2:
    def __init__(self):
        self.ev = []

    async def __call__(self, e):
        self.ev.append(e)


async def _e2e_cfg():
    return {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
            "agent.max_tool_rounds": 30, "agent.history_window": 40}


def _e2e_turn(content, tool_calls=None):
    return [{"type": "done", "message": {"role": "assistant", "content": content,
                                         "tool_calls": tool_calls}}]


def _e2e_tool_call(id_, name, **args):
    return {"id": id_, "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


async def _e2e_run(sf, sid, turns):
    """走真实 AgentRunner.run 路径（fake-llm，不打网络）：返回 (status, 事件流, llm)。

    ask 替身与 test_agent_loop 同形（asyncio.sleep(0)）：防未来脚本发 ask_user 轮次时
    await None 的 TypeError 被 run() 吞成没人看的 error 事件（复审 M-2）。
    """
    from app.agent.llm import ScriptedLLM
    from app.agent.loop import AgentRunner
    rec = _Rec2()
    llm = ScriptedLLM(turns)
    runner = AgentRunner(sf, llm, _e2e_cfg, rec,
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "帮我处理 PDF", asyncio.Event())
    return status, rec.ev, llm


async def test_full_chain_install_inject_read_disable_delete(client, tmp_path):
    """把 001/002(install)、003(enabled/delete)、004(prompt 注入)、005(受控读) 串成一条真实路径：
    读取路径从注入到 system 的文本里正则抽出 —— 从注入到读取
    全程经 AgentRunner 生产接线（含 _skills_root_of 拼 ToolContext，复审 I-1）。"""
    from sqlalchemy import text
    from app.agent.tools import ToolContext, execute_tool

    # 1) 安装（直链通道，MockTransport）
    client.app.state.skills_http_transport = _transport()
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/pdf/SKILL.md"})
    assert r.status_code == 201, r.text
    sk = r.json()
    skill_dir = Path(sk["skill_dir"])
    assert skill_dir.is_dir()

    # 取安装者 owner uid（技能行归属）+ 用同一 session_factory 建 owner 的工作区与会话
    sf = client.app.state.session_factory
    async with sf() as s:
        uid = (await s.execute(text(
            "SELECT owner_user_id FROM user_skill WHERE id=:i"), {"i": sk["id"]})).scalar()
        ws_root = tmp_path / "ws"
        ws_root.mkdir(exist_ok=True)
        ws = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES ('e2e', :p, :u) RETURNING id"),
            {"p": str(ws_root), "u": uid})).scalar()
        sid = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"), {"w": ws})).scalar()
        await s.commit()

    # 2) 真 AgentRunner.run：已启用技能名与绝对路径都出现在 <available_skills>
    status, evs, llm = await _e2e_run(sf, sid, [_e2e_turn("好了")])
    # run() 吞异常换状态码：状态不裁决则注入/读面段的失败会静默通过（复审 M-1）
    assert status == "idle", evs
    assert not [e for e in evs if e["type"] == "error"]
    sys_text = llm.received[0][0]["content"]
    assert "<available_skills>" in sys_text and sk["name"] in sys_text and str(skill_dir) in sys_text

    # 3) 受控读面经生产接线生效：读取路径从注入文本里抽取（真数据流绑定，复审 Minor-4），
    #    脚本轮次发 read_file tool_call，由 loop 内 _skills_root_of 拼的 ToolContext 执行
    m = re.search(r"(?m)^- name: .*\| path: (.+)$", sys_text)
    assert m, sys_text
    tc = _e2e_tool_call("call_1", "read_file", path=str(Path(m.group(1)) / "SKILL.md"))
    turns = [[{"type": "content_delta", "content": "读一下"},
              {"type": "done", "message": {"role": "assistant", "content": None, "tool_calls": [tc]}}],
             _e2e_turn("读完")]
    status2, evs2, _ = await _e2e_run(sf, sid, turns)
    assert status2 == "idle", evs2
    tr = [e for e in evs2 if e["type"] == "tool_result" and e["name"] == "read_file"]
    assert len(tr) == 1, evs2
    # _dispatch 把 ToolError 吞成 ok:False：先裁决返回形状再取键，失败时直接透出 error 文本
    assert tr[0]["result"].get("ok") is not False, tr[0]["result"]
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert tr[0]["result"]["content"] == body  # 全文一致性自证（size 断言无区分能力，复审 Minor-1）

    # 4) 故事12：工作区树永不可见技能母本（结构性守卫，非生产接线断言，复审 Minor-2）
    ld = await execute_tool(ToolContext(root=ws_root, session_id=sid, session_factory=sf,
                                        send=_Rec2()), "list_dir", {"path": "."})
    names = {e["name"] for e in ld["entries"]}
    assert sk["slug"] not in names and "data" not in names
    assert ws_root.resolve() not in skill_dir.resolve().parents  # 落盘位置与workspace无包含关系

    # 5) 停用→再构建 messages：技能从块中消失（故事10 enabled 驱动）
    rp = await client.patch(f"/api/skills/{sk['id']}", json={"enabled": False})
    assert rp.status_code == 200
    status3, evs3, llm3 = await _e2e_run(sf, sid, [_e2e_turn("再看")])
    assert status3 == "idle", evs3
    sys2_text = llm3.received[0][0]["content"]
    assert "<available_skills>" not in sys2_text and sk["name"] not in sys2_text

    # 6) 删除→行与盘同清（故事8 先删盘后删行）
    rd = await client.delete(f"/api/skills/{sk['id']}")
    assert rd.status_code == 200
    assert not skill_dir.exists()
    assert (await client.get("/api/skills")).json() == []
