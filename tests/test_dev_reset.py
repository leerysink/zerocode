# tests/test_dev_reset.py
import pytest
from app.api import apps


@pytest.mark.asyncio
async def test_reset_dev_drops_provisions_migrates(monkeypatch):
    calls = []

    async def fake_drop_env(sf, slug, env):
        calls.append(("drop", env))

    async def fake_provision(sf, slug, env):
        calls.append(("provision", env))
        return {"db": f"{slug}_{env}", "user": "u", "password": "p"}

    async def fake_root(sf, app):
        return "ROOT"

    async def fake_run_sqlchange(root, env, cred, on_log=None):
        calls.append(("migrate", env))
        return {"ok": True, "code": 0, "log": "ALL DONE"}

    monkeypatch.setattr(apps.provision, "drop_env", fake_drop_env)
    monkeypatch.setattr(apps.provision, "provision_env", fake_provision)
    monkeypatch.setattr(apps.sqlrun, "run_sqlchange", fake_run_sqlchange)
    monkeypatch.setattr(apps.engine, "_app_root", fake_root)

    events = []

    class Hub:
        def publish(self, aid, ev):
            events.append(ev)

    await apps._reset_dev(object(), {"id": 1, "slug": "demo"}, Hub())
    assert calls == [("drop", "dev"), ("provision", "dev"), ("migrate", "dev")]
    assert events[-1]["type"] == "dev_reset_result" and events[-1]["ok"] is True


@pytest.mark.asyncio
async def test_reset_dev_reports_failure(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("docker 掉了")

    monkeypatch.setattr(apps.provision, "drop_env", boom)
    events = []

    class Hub:
        def publish(self, aid, ev):
            events.append(ev)

    await apps._reset_dev(object(), {"id": 2, "slug": "demo2"}, Hub())
    assert events[-1]["type"] == "dev_reset_result" and events[-1]["ok"] is False
    assert "docker 掉了" in events[-1]["log"]
