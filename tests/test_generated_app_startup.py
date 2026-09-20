# tests/test_generated_app_startup.py
"""已生成应用的启动日志（真跑一遍 backend 的 lifespan，不起容器、不连库）。

apps/test1 是脚手架产物的现网样本：进程一起来就要往 stdout 打 banner（compose logs 与平台
预览日志流采集的都是 stdout），字段含应用名、监听端口、健康检查、数据库与连通性、就绪耗时；
连不上库只降级成一行 WARNING，绝不阻断启动；连接串口令绝不进日志。
"""
import importlib.util
import logging
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "apps" / "test1" / "backend" / "app" / "main.py"


@pytest.fixture
def load_backend(monkeypatch):
    """按环境变量加载 apps/test1 的 backend 模块；结束还原根 logger（模块 import 时 force 过）。"""
    saved_handlers, saved_level = list(logging.root.handlers), logging.root.level

    def _load(**env):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("APP_PORT", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        spec = importlib.util.spec_from_file_location("zc_generated_app_main", BACKEND)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    yield _load
    logging.root.handlers[:] = saved_handlers
    logging.root.setLevel(saved_level)


async def test_startup_banner_fields(load_backend, capsys):
    """无 DATABASE_URL（裸跑）也要完整打出 banner，并把「跳过连通性」讲清楚。"""
    mod = load_backend(APP_PORT="8000")
    async with mod.lifespan(mod.app):
        pass
    out = capsys.readouterr().out
    assert "陌如玉（test1）后端启动" in out
    assert "http://0.0.0.0:8000" in out
    assert "/api/health" in out
    assert "未注入 DATABASE_URL" in out
    assert "跳过（DATABASE_URL 为空）" in out
    assert "就绪耗时" in out
    assert "陌如玉 后端停止" in out            # 关停也留一行，便于对齐启动次数
    assert "[test1]" in out                    # 行前缀带 slug，多应用同屏可分辨


async def test_db_unreachable_degrades_to_warning(load_backend, capsys):
    """端口 1 必然连不上：启动不被打断，连通性一行讲明原因。"""
    mod = load_backend(APP_PORT="8000",
                       DATABASE_URL="postgresql+asyncpg://someone:top-secret@127.0.0.1:1/test1_dev")
    async with mod.lifespan(mod.app):          # 不抛异常即通过（banner 自己吸收失败）
        pass
    out = capsys.readouterr().out
    assert "WARNING" in out and "不可用" in out
    assert "postgresql+asyncpg://127.0.0.1:1/test1_dev" in out   # 脱敏形态：驱动/主机/端口/库名
    assert "top-secret" not in out and "someone" not in out      # 口令与账号绝不进日志


async def test_env_fields_come_from_environment(load_backend, capsys):
    """APP_ENV / PORT 兜底：环境名与端口都取自平台注入的环境变量。"""
    mod = load_backend(APP_ENV="prod", PORT="8000")
    async with mod.lifespan(mod.app):
        pass
    out = capsys.readouterr().out
    assert "运行环境 : prod" in out
    assert "http://0.0.0.0:8000" in out


async def test_missing_app_port_says_so(load_backend, capsys):
    """平台未注入 APP_PORT（手工 uvicorn 直跑）：端口一行讲明原因，而不是打个 `?`。"""
    mod = load_backend()
    async with mod.lifespan(mod.app):
        pass
    out = capsys.readouterr().out
    assert "由启动参数决定（平台未注入 APP_PORT）" in out
    assert "监听地址 : http://0.0.0.0:?" not in out
