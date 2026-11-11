import os
from pathlib import Path

DEFAULT_DATABASE_URL = "postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode"
TEST_DATABASE_URL = "postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode_test"

def database_url() -> str:
    return os.environ.get("ZEROCODE_DATABASE_URL", DEFAULT_DATABASE_URL)

def test_database_url() -> str:
    return os.environ.get("ZEROCODE_TEST_DATABASE_URL", TEST_DATABASE_URL)

HOST = os.environ.get("ZEROCODE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZEROCODE_PORT", "8100"))

_REPO_ROOT = Path(__file__).resolve().parents[1]

def data_dir() -> Path:
    """平台数据区根（技能母本等运行时落盘）。ZEROCODE_DATA_DIR 覆盖，默认仓库根 data/。

    每次调用现读环境变量（非 import 期固化的 HOST/PORT），便于测试用 tmp_path 隔离落盘。"""
    return Path(os.environ.get("ZEROCODE_DATA_DIR") or (_REPO_ROOT / "data"))


def github_token() -> str | None:
    """GitHub API 可选 token（缓解 rate limit）；未配置则匿名。现读便于测试注入。"""
    return os.environ.get("ZEROCODE_GITHUB_TOKEN") or None


def github_mirror() -> str | None:
    """可选 GitHub raw 加速前缀（如 https://gh-proxy.com/），只作用于文件下载，不碰 API。"""
    return (os.environ.get("ZEROCODE_GITHUB_MIRROR") or "").strip().rstrip("/") or None


def normalize_no_proxy() -> None:
    """去掉 NO_PROXY 里 IPv6 地址的方括号。

    httpx 0.28 把每个 NO_PROXY 项当 URL 解析，`[::1]` 会被读成非法端口，导致
    AsyncClient 构造时直接抛 InvalidURL（与请求目标无关）。`::1` 语义相同且能被解析，
    保留 trust_env 意味着环境代理能力不受影响。
    """
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        items = [p.strip() for p in raw.split(",") if p.strip()]
        os.environ[key] = ",".join(
            p[1:-1] if p.startswith("[") and p.endswith("]") else p for p in items)


normalize_no_proxy()  # import 即生效：任何 httpx 客户端构造前都先经这里
