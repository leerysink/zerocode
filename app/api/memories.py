"""记忆 HTTP API（ticket 007 / spec 故事 11、12、18、19、21）。

工作区级端点挂在 `/api/workspaces/{workspace_id}/memory`，面板（008）与 memory_write 工具
读写的是同一张 `workspace_memory`——不存在第二套存储，面板里加一条，下一轮召回就带上它。

三道口径全部沿用既有接缝，一处都不新立：
- 鉴权：全局 auth_guard 把未登录拦在业务层之前（401），本模块只从 `request.state.actor` 取身份；
- 归属：创始人 bypass（metrics/store 同款），越权与不存在共用一个 404，不给探测留差异；
- 写入红线：脱敏与结构逃逸中和在服务层 `create`/`update` 里，路由不重复实现，也绕不过去。

路由层刻意不写业务 SQL：读写全部经 `memory_service`，owner 条件由 `require_workspace` 收口，
避免出现「工具侧一道闸、API 侧另一道闸」的双份实现。

归属闸挂在 **router 级 dependencies** 上（`guard_workspace`），不靠 handler 自觉调用：服务层的
`create`/`update`/`delete`/`list_memories` 自己不查归属，而「记得先过闸」这种约束在新增
handler 时一定会漏，漏一次就是静默的跳账号读写（007 二轮 review M-3 定为机械强制）。
取舍登记在 `memory_service` 模块 docstring。
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.deploy.errors import DeployError
from app.services import memory_service


def _workspace_id(raw: str) -> int:
    """路径里的 workspace_id 统一走服务层的 `parse_id`，不声明成 `int` 路径参数。

    两个 500 都从这里出：① pydantic 的 `int` 不上界到 int64，`9223372036854775808` 会原样
    绑进 asyncpg 的 bigint 列，抛 DataError；② 几千位的纯数字串在 `int()` 里抛 ValueError
    （CPython 的 `sys.set_int_max_str_digits`），路由的 `except DeployError` 接不住。
    和 memory_id 共用一个解析器，也顺带把「非法 id」的回显口径拉成一种（都是 400）。
    """
    return memory_service.parse_id(raw, "工作区")


def _owner(request: Request):
    """创始人传 None = 服务层不加归属过滤（与 metrics/store 的可见性口径一致）。"""
    actor = request.state.actor
    return None if actor.get("is_founder") else actor["id"]


async def guard_workspace(request: Request, workspace_id: str) -> None:
    """router 级归属闸：所有本前缀下的路由（含以后新增的）自动过闸，handler 里漏不掉。

    两道自己的规矩：
    ① 必须把 `DeployError` 换成 `HTTPException`——依赖不在 handler 的 `try` 范围内，原样抛
       出去就是 500，坏 id（400）和越权/不存在（404）全都会走这条路；
    ② 判序是「闸先于 handler 的入参校验」：越权工作区配上 schema 非法的 body / query 得到 404 而不是 422。
       唯一例外是 JSON 语法本身解不开——FastAPI 在读 body 阶段就抛 422，闸不跑；该答复与工作区是否存在无关，不构成探测面。
    """
    try:
        await memory_service.require_workspace(
            request.app.state.session_factory, _workspace_id(workspace_id), _owner(request))
    except DeployError as e:
        raise HTTPException(e.status, str(e))


# 归属闸写在 router 级 dependencies 上，而不是逐个 handler 里 `await require_workspace(...)`：
# 后者每新增一个端点都要重新记得一次，前者新增的端点默认就在闸后。
router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/memory",
    dependencies=[Depends(guard_workspace)],
)


class MemoryIn(BaseModel):
    type: str
    content: str
    title: str | None = None
    enabled: bool = True


class MemoryPatchIn(BaseModel):
    """局部更新：没传的字段保持原值。四个字段全不传由服务层拒掉（400），
    不能只刷一个 updated_at 让调用方以为改成了。

    null = 不改，而不是「改成空」；`title` 想要清空得显式传 `""`（服务层把空白 title 归一成 NULL），
    `type`/`content` 传 null 也同理是保持原值。"""
    type: str | None = None
    content: str | None = None
    title: str | None = None
    enabled: bool | None = None


def _row_json(row: dict) -> dict:
    """BIGSERIAL 的 id / workspace_id 一律下发字符串：19 位整数过 JS Number 会被悄悄截断，
    前端拿截断值再发回来就改错行了。前端全程按字符串透传，不做 Number()。"""
    out = dict(row)
    out["id"] = str(out["id"])
    out["workspace_id"] = str(out["workspace_id"])
    return out


@router.get("")
async def list_memories(request: Request, workspace_id: str, response: Response,
                        limit: int | None = None):
    """面板列表：含停用行（关掉的记忆要看得见才能再打开），新→旧，条数封顶。

    返回体维持裸数组（与 skills/models 同形，前端不需要两套写法），截断信号走响应头：
    `X-Memory-Truncated: true` 表示还有没取回的老记忆（面板没分页，不提示就是用户眼里的数据丢失），
    `X-Memory-Limit` 告知本次实际生效的封顶值。只在 200 分支设头，400/404 不带。
    """
    sf = request.app.state.session_factory
    try:
        wid = _workspace_id(workspace_id)
        # 默认值在调用时从服务层读，不在 def 时绑定成默认参数，也不在本模块另存一份全局常量：
        # 后者会变成第二处默认值（改服层忘改这里就是静默错位），前者则让测试没法调小它
        # 来证明「不传 limit 也不是无限全量」（见 tests/test_memory_api.py）。
        size = memory_service.PANEL_LIMIT_DEFAULT if limit is None else limit
        rows, truncated = await memory_service.list_memories(sf, wid, size)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    response.headers["X-Memory-Truncated"] = "true" if truncated else "false"
    # 封顶值用服务层刚校过的 `size` 原样回显，不再二次调 `check_panel_limit`：
    # 那道校验的 400 依赖 try 包裹，把它搬到 try 之外面去就是自己埋一个 500。
    response.headers["X-Memory-Limit"] = str(size)
    return [_row_json(r) for r in rows]


@router.post("", status_code=201)
async def create_memory(request: Request, workspace_id: str, body: MemoryIn):
    sf = request.app.state.session_factory
    try:
        wid = _workspace_id(workspace_id)
        row = await memory_service.create(sf, wid, type=body.type, content=body.content,
                                          title=body.title, enabled=body.enabled)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    return _row_json(row)


@router.patch("/{memory_id}")
async def update_memory(request: Request, workspace_id: str, memory_id: str, body: MemoryPatchIn):
    """memory_id 保持字符串进服务层：`parse_id` 只认纯 ASCII 数字，全角/负数/拼接串在这里
    拦成 400，换成路径参数 int 会把全角「１２」悄悄换成 12 去改别人的行。"""
    sf = request.app.state.session_factory
    try:
        wid = _workspace_id(workspace_id)
        row = await memory_service.update(sf, wid, memory_id, type=body.type,
                                          content=body.content, title=body.title,
                                          enabled=body.enabled)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    return _row_json(row)


@router.delete("/{memory_id}")
async def delete_memory(request: Request, workspace_id: str, memory_id: str):
    """面板专用硬删（D-5）：工具侧的 delete 默认软删，用户在面板上点删除就是要它真没了。
    第二次删同一个 id 因此是 404，而不是幂等的 200。"""
    sf = request.app.state.session_factory
    try:
        wid = _workspace_id(workspace_id)
        row = await memory_service.delete(sf, wid, memory_id, hard=True)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    return _row_json(row)
