# app/deploy/errors.py
class DeployError(Exception):
    """部署域业务错误；status 用于 REST 映射；payload 为结构化负载（如破坏性清单），
    供失败收口事件透传给前端做分支渲染（不影响 str(e) 的人类可读文案）。"""
    def __init__(self, message: str, status: int = 400, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload
