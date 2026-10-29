# app/deploy/logs.py
"""dev 预览日志的两路归并：平台步骤行（DeployHub 环形缓冲）+ 容器输出（compose logs）。

应用页「查看日志」面板（api/dev_logs.py）与 Agent 的 `preview_logs` 工具读的是**同一份**结果：
两路各自维护一份归并逻辑早晚漂移，而「Agent 看到的日志」与「用户看到的日志」不一致会让排障
变成两个人各说各话。本模块只做收集与归并，不做限长/遮断/整形——那是各出口的口径
（面板要 1000 行原文，工具要 300 行遮断后的短行）。
"""
from datetime import datetime, timezone

from app.deploy import engine

MAX_TAIL = 1000        # 日志面板要的是「最近发生了什么」，不是全量转储


def iso(epoch) -> str:
    """环形缓冲存的是 epoch 秒；给人看的时间转成本地时区 + 秒精度，解析不了就留空。"""
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone().isoformat(
            timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


async def collect_dev_logs(session_factory, app: dict, hub, *,
                           tail: int = 200, since_seq: int = 0,
                           container_timeout: int = 60) -> dict:
    """取某应用最近的 dev 日志（平台步骤行 + 容器输出，按时间归并）。

    `tail` 由调用方自行夹取（面板 1..1000，Agent 工具 10..300）；`since_seq` 只要平台侧
    seq 大于它的行。容器侧拿不到（Docker 没开 / 栈没起）**不报错**：note 讲明原因，
    步骤行照样返回——那正是应用起不来时唯一看得见的线索。

    `container_timeout` 默认 60s（REST 面板）；Agent 工具传 15s，避免一次日志读取卡住整轮对话。
    """
    n = max(int(tail), 1)
    since = max(int(since_seq), 0)
    platform = [{"seq": r["seq"], "ts": iso(r["epoch"]), "epoch": r["epoch"],
                 "source": "platform", "service": "平台", "line": r["line"]}
                for r in hub.tail_logs(app["id"], limit=n, since_seq=since)]
    container, note = await engine.container_logs(session_factory, app, n, timeout=container_timeout)
    lines = engine.merge_logs(platform, container)
    return {"lines": lines, "note": note, "status_dev": app.get("status_dev"),
            "url": app.get("preview_url") or "",
            "last_seq": platform[-1]["seq"] if platform else since}
