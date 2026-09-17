import os

DEFAULT_DATABASE_URL = "postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode"
TEST_DATABASE_URL = "postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode_test"

def database_url() -> str:
    return os.environ.get("ZEROCODE_DATABASE_URL", DEFAULT_DATABASE_URL)

def test_database_url() -> str:
    return os.environ.get("ZEROCODE_TEST_DATABASE_URL", TEST_DATABASE_URL)

HOST = os.environ.get("ZEROCODE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZEROCODE_PORT", "8100"))
