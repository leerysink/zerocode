"""工作区遍历忽略表：文件树（`app/api/files.py`）与 Agent 检索工具共用同一张名单。

两边各抄一份必然漂移——search 搜得到而文件树看不见，或反过来。口径注释留在两份调用点里，
这里只放名单本身与判定。
"""
from __future__ import annotations

IGNORE_DIRS = {".git", "node_modules", "__pycache__", "dist", ".venv", "build"}
# 大型栈 dev 预览的必然产物：compose 把 backend/ 挂进容器就地跑 mvn，编译产物与 logback 日志
# 直接落在应用工作区里（fastapi 栈的 uvicorn 不产这些）。不收它们，一次预览之后文件树就会被
# 几千个 .class 与滚动的日志页满。
# 按路径尾屏蔽而非裸名：target / logs 都是业务通用词（目标管理、日志页面），按名屏蔽会把用户
# 自己的 src/views/target 一类目录在文件管理器里永久藏掉。这两个产物的落点由「dev 容器把 ../backend
# 挂成 working_dir」与模板里 logging.path 的默认值共同决定（${user.dir}/logs 即 backend/logs）；
# 两者任一变动（拆多模块、改日志目录）都要回来对一眼这两份名单。
# 本名单刻意不含 backend/uploads：上传件不进包也不进指纹（packages.py 的裁定），但用户得在文件
# 管理器里看见自己传上去的东西。“不收”与“不可见”是两件事，想顺手把两边对齐前先读 packages.py 那段。
IGNORE_PATH_TAILS = (("backend", "target"), ("backend", "logs"))


def is_ignored_dir(rel_parts: tuple) -> bool:
    """某个目录节点是否不进文件树：按名屏蔽的通用名单，加按路径尾屏蔽的构建产物。

    这里判的是目录节点本身（而非 packages.py 那样判文件）：一旦 backend/target 被剪掉，
    它下面的任何东西都递不到，所以只需尾匹配。代价是一条口径差：若 backend/ 下真出现一个叫
    target 的**文件**（maven 不造这种东西），文件树看得见、而包与 source_hash 不收。
    """
    if not rel_parts:
        return False
    return (rel_parts[-1] in IGNORE_DIRS
            or any(rel_parts[-len(t):] == t for t in IGNORE_PATH_TAILS))
