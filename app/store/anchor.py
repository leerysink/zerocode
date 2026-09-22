# app/store/anchor.py
"""数据轴真相（规格 M3-a §2.3 / §6.1）。

锚点取自库的 sqlchange_history，而非 apps/<slug>/sqlchange/ 的目录列表：后者里混着 dev
跑过但从未发布的半途迁移，进包就等于把垃圾推给所有申请人。
"""
import hashlib
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app import config as app_config

HISTORY_SQL = ("SELECT filename, checksum FROM sqlchange_history WHERE success "
               "ORDER BY filename")


def app_db_url(cred: dict) -> str:
    """应用库连接串：复用平台实例的 host/port（不写死 5433），只换库名/角色/密码。

    SQLAlchemy 默认掩码密码，取串必须 hide_password=False。
    """
    base = make_url(app_config.database_url())
    return base.set(database=cred["db"], username=cred["user"],
                    password=cred["password"]).render_as_string(hide_password=False)


async def read_history(url: str) -> list[dict]:
    """不吞异常：表不存在/库不可达都要如实上抛，由上架闸门给出准确原因。"""
    eng = create_async_engine(url)
    try:
        async with eng.connect() as c:
            rows = (await c.execute(text(HISTORY_SQL))).mappings().all()
        return [{"filename": r["filename"], "checksum": r["checksum"] or ""} for r in rows]
    finally:
        await eng.dispose()


def file_md5(path) -> str:
    """与 run.sh 的 md5sum 同口径：原始字节，绝不做换行归一（CRLF 差异会误判篡改）。"""
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def names_of(anchor: list[dict]) -> list[str]:
    return [e["filename"] for e in anchor]


def fingerprint(anchor: list[dict]) -> str:
    h = hashlib.sha256()
    for e in anchor:
        h.update(f"{e['filename']}:{e['checksum']}\n".encode("utf-8"))
    return h.hexdigest()


def is_prefix(short: list[str], long: list[str]) -> bool:
    """约束 L：短序列必须是长序列的前缀（文件名升序即时间序，与 run.sh 的 ls|sort 同判定）。"""
    return list(long[:len(short)]) == list(short)


def mismatches(anchor: list[dict], on_disk: dict[str, str]) -> list[str]:
    """已应用文件在盘上被改过/删掉 → 违反纪律 1，拒绝上架（§6.1 闸门 3）。"""
    out = []
    for e in anchor:
        if e["filename"] not in on_disk:
            out.append(f"{e['filename']}（文件已不在 sqlchange/）")
        elif on_disk[e["filename"]] != e["checksum"]:
            out.append(e["filename"])
    return out
