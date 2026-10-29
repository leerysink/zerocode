# app/deploy/ports.py
import re, socket
from sqlalchemy import text
from app.deploy.errors import DeployError
from app.deploy.settings import preview_probe_host

def port_free(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """TCP connect 探测：连不上视为空闲（listen 探测语义，规格 05 §4.1）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return False
    except OSError:
        return True

async def allocate_pair(session, probe=None) -> tuple[int, int]:
    """在 app_config 的端口段内成对分配（prod=偶, dev=奇）；跳过已登记与探测占用的端口。

    默认探针连 `preview_probe_host()` 而不是 127.0.0.1：平台跑在容器里时后者是容器自己的 loopback，
    宿主上已发布的应用端口全都「探起来空闲」，探测这道闸等于没有。
    """
    probe = probe or (lambda port: port_free(port, host=preview_probe_host()))
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


# 「库里没登记」不等于「宿主上空闲」：栈端口被一个库外容器占着时 compose up 直接甩 daemon 原文。
# 那一行里的端口号是唯一线索，不解析出来就没法告诉用户该杀谁。
BIND_CONFLICT_RE = re.compile(r"Bind for \S+?:(\d+) failed: port is already allocated")


def parse_bind_conflict(log: str) -> int | None:
    """取 compose 输出里绑不上的宿主端口；不是绑端口失败则返回 None。"""
    m = BIND_CONFLICT_RE.search(log or "")
    return int(m.group(1)) if m else None


async def port_holder(port: int, run) -> str:
    """查出把该宿主端口发布掉的容器名；没人发布或问不动 docker 时返回空串。

    `run` 收 engine.docker：本模块不 import engine（engine 侧要用它的地方都在起栈路径上，
    反向依赖会把 deploy 包绕成环），测试传替身即可点名。
    """
    code, out = await run("ps", "--filter", f"publish={port}", "--format", "{{.Names}}")
    if code != 0:
        return ""
    return next((line.strip() for line in (out or "").splitlines() if line.strip()), "")


async def explain_start_failure(log: str, run) -> str:
    """compose up 失败的可读原因：绑端口冲突点名占用者，其余失败照旧给日志尾部。

    原文一律附在后面——点名靠的是 `docker ps`，它挂了或结论不对时用户还得能看到 daemon 说了什么。
    """
    tail = (log or "").strip()[-800:]
    port = parse_bind_conflict(log)
    if port is None:
        return tail
    holder = await port_holder(port, run)
    if holder:
        return (f"宿主端口 {port} 已被容器 {holder} 占用（多半是已删应用的残留栈）："
                f"docker rm -f {holder} 后重试。\n原始输出:\n{tail}")
    return (f"宿主端口 {port} 已被占用且不在 docker ps 里（多半是宿主上的进程）："
            f"释放它或在设置里换一段端口后重试。\n原始输出:\n{tail}")
