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
