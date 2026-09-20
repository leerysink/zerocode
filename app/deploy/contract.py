"""应用契约（规格 02 §5）：激活应用工作区时注入 AI 系统提示词的规范文本。"""
import json, logging
from pathlib import Path

logger = logging.getLogger(__name__)

STACK_LABEL = {"vue3-fastapi": "Vue3 + FastAPI", "vue3-node": "Vue3 + Express",
               "static": "纯前端静态页", "vue3-springboot": "Vue3 + Spring Boot 3"}
ENV_HINT = {"vue3-fastapi": "DATABASE_URL（SQLAlchemy asyncpg 连接串）与 APP_PORT",
            "vue3-node": "DATABASE_URL（pg 连接串）与 APP_PORT",
            "vue3-springboot": "SPRING_DATASOURCE_URL / SPRING_DATASOURCE_USERNAME / SPRING_DATASOURCE_PASSWORD / SERVER_PORT",
            "static": "（无后端）"}

def build_app_contract(root: Path) -> str | None:
    f = root / "app.json"
    if not f.is_file():
        return None
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("app.json 解析失败，契约未注入: %s", f)
        return None
    stack = meta.get("stack", "")
    ports = meta.get("ports", {})
    return (
        f"\n\n【应用契约】当前工作区是 zerocode 应用工程「{meta.get('name')}」（{meta.get('slug')}，"
        f"{STACK_LABEL.get(stack, stack)}，dev 预览端口 {ports.get('dev')}）。\n"
        "1) 目录：frontend/（Vue3+ant-design-vue）、backend/（后端工程，static 栈无）、sqlchange/、deploy/。\n"
        "2) 技术栈与版本以现有脚手架为准，只能追加依赖，不得替换或降级已锁定版本。\n"
        f"3) 一切数据库结构或数据变更必须写成 sqlchange/V<YYYYMMDD>_<两位序号>_<说明>.sql"
        "（写前查看目录取下一个序号；一个文件一个逻辑变更；文件头注明用途与影响表；禁止改写已存在的迁移文件）；"
        "代码里不得出现建表/改表语句，不得硬编码数据库连接。\n"
        f"4) 后端从环境变量读取 {ENV_HINT.get(stack, '平台注入的环境变量')}；必须保持 /api 前缀与 health 接口"
        f"（{meta.get('health', {}).get('backend', '')}）可用。\n"
        "5) 前端页面用 ant-design-vue，中文界面，表格用 columns+bodyCell 模式，弹窗表单 label 宽 120px 右对齐。\n"
        "6) deploy/ 与 app.json 由平台管理，禁止读写。\n"
        "7) 完成功能后告知用户：可点「预览」查看效果，正式使用请点「发布」。")
