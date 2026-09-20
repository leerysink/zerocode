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

def _db_facts(db) -> str:
    """meta["database"] → dev/prod 库名与用户各一行事实；缺失或形态异常则降级为空（不抛）。

    app.json 本无口令，列库名/用户是安全事实，且与「连接串由平台注入」的约束同行陈述。
    """
    lines = []
    if isinstance(db, dict):
        for env in ("dev", "prod"):
            entry = db.get(env)
            if not isinstance(entry, dict):
                continue
            name, user = entry.get("name"), entry.get("user")
            if not name:
                continue
            seg = f"- {env} 库：{name}"
            if user:
                seg += f"（用户 {user}）"
            lines.append(seg)
    if not lines:
        return ""
    return ("本应用数据库（app.json 不含口令，连接串一律由平台注入环境变量，代码不得硬编码；"
            "库名与用户仅供理解，代码与 SQL 不得引用库名/用户名）：\n"
            + "\n".join(lines) + "\n")

def build_app_contract(root: Path) -> str | None:
    """读 root/app.json 渲染契约文本；任何损坏/异常形态（缺文件、非法 JSON、顶层非对象、
    字段形态错乱、读取 IO 错误）都不向调用方抛异常，统一返回 None 表示「不注入」。
    准入闸门：仅平台清单（schema=zc-app/1 且 slug 非空）才注入，外来 app.json 与残缺字段静默跳过。"""
    try:
        f = root / "app.json"
        if not f.is_file():
            return None
        meta = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):          # 合法 JSON 但顶层非对象（如 [1,2]、"x"）
            logger.warning("app.json 顶层不是对象，契约未注入: %s", root)
            return None
        if meta.get("schema") != "zc-app/1" or not meta.get("slug"):
            logger.warning("app.json 非 zc-app/1 清单或缺 slug，契约未注入: %s", root)
            return None
        stack = meta.get("stack", "")
        ports = meta.get("ports")
        health = meta.get("health")
        if not isinstance(ports, dict):
            ports = {}
        if not isinstance(health, dict):
            health = {}
        return (
            f"\n\n【应用契约】当前工作区是 zerocode 应用工程「{meta.get('name')}」（{meta.get('slug')}，"
            f"{STACK_LABEL.get(stack, stack)}，dev 预览端口 {ports.get('dev')}）。\n"
            + _db_facts(meta.get("database")) +
            "1) 目录：frontend/（Vue3+ant-design-vue）、backend/（后端工程，static 栈无）、sqlchange/、deploy/。\n"
            "2) 技术栈与版本以现有脚手架为准，只能追加依赖，不得替换或降级已锁定版本。\n"
            f"3) 一切数据库结构或数据变更必须写成 sqlchange/V<YYYYMMDD>_<两位序号>_<说明>.sql"
            "（写前查看目录取下一个序号；一个文件一个逻辑变更；文件头注明用途与影响表；禁止改写已存在的迁移文件）；"
            "代码里不得出现建表/改表语句，不得硬编码数据库连接；"
            "sqlchange 文件在单事务内执行，建索引写普通 CREATE INDEX，不得使用 CREATE INDEX CONCURRENTLY。\n"
            f"4) 后端从环境变量读取 {ENV_HINT.get(stack, '平台注入的环境变量')}；必须保持 /api 前缀与 health 接口"
            f"（{health.get('backend', '')}）可用。\n"
            "5) 前端页面用 ant-design-vue，中文界面，表格用 columns+bodyCell 模式，弹窗表单 label 宽 120px 右对齐。\n"
            "6) deploy/ 与 app.json 由平台管理，禁止读写。\n"
            "7) 完成功能后告知用户：可点「预览」查看效果，正式使用请点「发布」。")
    except Exception:
        logger.warning("app.json 读取或解析失败，契约未注入: %s", root)
        return None
