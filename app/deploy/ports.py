# app/deploy/ports.py
import socket
from sqlalchemy import text
from app.deploy.errors import DeployError

def port_free(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """TCP connect 探测：连不上视为空闲（listen 探测语义，规格 05 §4.1）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return False
    except OSError:
        return True

async def allocate_pair(session, probe=None) -> tuple[int, int]:
    """在 app_config 的端口段内成对分配（prod=偶, dev=奇）；跳过已登记与探测占用的端口。"""
    probe = probe or port_free
    base = int((await session.execute(text(
        "SELECT value FROM app_config WHERE key='deploy.port_base'"))).scalar() or 8210)
    top = int((await session.execute(text(
        "SELECT value FROM app_config WHERE key='deploy.port_max'"))).scalar() or 8399)
    if base < 1024 or top <= base:
        raise DeployError(f"端口段非法: {base}-{top}")
    used = set((await session.execute(text(
        "SELECT prod_port, dev_port FROM app_project"))).fetchall())
    used = {p for pair in used for p in pair}
    for prod in range(base if base % 2 == 0 else base + 1, top, 2):
        dev = prod + 1
        if prod not in used and dev not in used and probe(prod) and probe(dev):
            return prod, dev
    raise DeployError("端口段已用尽，请在设置中扩大端口范围", status=409)
