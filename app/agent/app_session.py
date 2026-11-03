"""验证会话：后端代登应用，只把 JWT 交给 sidecar（ADR-0038）。

口令是 vue3-springboot 模板种子里的公开约定（`app/deploy/contract.py` 已对模型陈述），
不落库、不进日志、不传 sidecar。登录失败是正常路径（小栈模板没有这个接口）。
"""
import httpx

from app.deploy.settings import preview_probe_host

LOGIN_PATH = "/api/auth/login"
LOGIN_TIMEOUT_SECONDS = 5.0
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
NO_LOGIN_NOTE = "该应用没有可用的登录接口"


def parse_login_token(data) -> str:
    """从 `{code, msg, data:{token}}` 信封里取 token；形状不对一律返回空串。"""
    if not isinstance(data, dict):
        return ""
    payload = data.get("data")
    if not isinstance(payload, dict):
        return ""
    token = payload.get("token")
    return token.strip() if isinstance(token, str) else ""


async def login_app_session(dev_port, *, timeout_s: float = LOGIN_TIMEOUT_SECONDS,
                            client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """返回 `(token, note)`；失败时 token 为空、note 说明原因。note 不含口令。"""
    url = f"http://{preview_probe_host()}:{int(dev_port)}{LOGIN_PATH}"
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        resp = await client.post(url, json={"username": ADMIN_USERNAME,
                                            "password": ADMIN_PASSWORD})
    except httpx.TimeoutException:
        return "", "登录请求超时"
    except httpx.HTTPError:
        return "", "无法连接应用的登录接口"
    finally:
        if own_client:
            await client.aclose()
    if resp.status_code in (404, 405):
        return "", NO_LOGIN_NOTE
    if resp.status_code != 200:
        return "", f"登录接口返回 HTTP {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return "", "登录接口返回的不是 JSON"
    token = parse_login_token(data)
    if not token:
        return "", "登录接口没有返回 token"
    return token, ""
