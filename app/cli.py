# app/cli.py
"""运维 CLI 入口（Ticket 004）：python -m app.cli reset-password <昵称> [新口令]

面向"忘记密码"的本地逃生门——登录体系无邮件/短信找回通道，唯一权威重置手段是
能直连数据库的命令行。口令参数可省略（省略时交互式 getpass，避免落 shell 历史）。
"""
import argparse
import asyncio
import sys

from sqlalchemy import text

from app.db import make_engine, make_session_factory
from app.store.identity import hash_password


async def reset_password(session_factory, nickname: str, new_password: str) -> bool:
    """按昵称重置口令哈希；昵称不存在返回 False（token 不轮换，现有会话不受影响）。"""
    async with session_factory() as s:
        r = (await s.execute(text(
            "UPDATE actor SET password_hash = :p WHERE nickname = :n RETURNING id"),
            {"p": hash_password(new_password), "n": (nickname or "").strip()})).first()
        await s.commit()
    return r is not None


async def _amain(args: argparse.Namespace) -> int:
    password = args.password
    if not password:
        import getpass
        password = getpass.getpass("新口令: ")
        if not password:
            print("口令不能为空", file=sys.stderr)
            return 2
    engine = make_engine()
    try:
        sf = make_session_factory(engine)
        ok = await reset_password(sf, args.nickname, password)
    finally:
        await engine.dispose()
    if not ok:
        print(f"用户不存在: {args.nickname}", file=sys.stderr)
        return 1
    print(f"已重置 {args.nickname} 的口令")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="zerocode 运维命令")
    sub = parser.add_subparsers(dest="command", required=True)
    p_reset = sub.add_parser("reset-password", help="按昵称重置登录口令")
    p_reset.add_argument("nickname")
    p_reset.add_argument("password", nargs="?", default=None,
                         help="省略则交互式输入（不进 shell 历史）")
    args = parser.parse_args(argv)
    if args.command == "reset-password":
        return asyncio.run(_amain(args))
    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
