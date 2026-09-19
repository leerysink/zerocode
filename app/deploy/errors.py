# app/deploy/errors.py
class DeployError(Exception):
    """部署域业务错误；status 用于 REST 映射。"""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
