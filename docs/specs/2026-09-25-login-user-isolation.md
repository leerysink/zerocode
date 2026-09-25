# Spec：本地账号体系与用户隔离（login & user isolation）

日期：2026-09-25 ｜ 状态：待评审
前置共识：ADR-0003（actor 升级为登录用户）、ADR-0004（先登录后技能）
术语：见 `CONTEXT.md`（技能 skill、actor、控制台 console）

## 1. 问题陈述

zerocode 平台目前是"无主"的：workspace、会话、应用（app_project）、模型配置（app_config）
全部全局共享，唯一的身份 actor 只是应用商店的轻身份（昵称+token，不鉴权，
`app/store/identity.py` 注释甚至明确"不得演化成授权依据"——本 spec 正式推翻该约束）。

后续的**技能功能**（账户级技能库、从网上安装 SKILL.md）要求"一切归用户"，
没有登录与归属就没有技能的挂载点。因此登录体系是技能项目的前置依赖。

## 2. 方案概述

- **actor 即用户**：不新建 user 表，actor 表加 `password_hash`，token 升级为会话凭据；
  应用商店既有外键（applicant_id / owner_actor / created_by）不动。
- **本地账号体系**：注册/登录/验证全部在本机实例内完成（bcrypt 口令 + HttpOnly cookie），
  不依赖云端服务。运行假设仍是"每人本地一个实例"，但实例内多账号完全隔离。
- **归属维度下沉**：顶层资源（workspace / app_project / 用户级配置）加 `owner_user_id`，
  子资源（chat_session / message / file_change）经父资源传递隔离，不加列。
- **创始人机制**：首个注册账号自动成为创始人（`is_founder`），注册成功时单事务承接
  全部存量无主数据与全局 llm 配置。
- 交付顺序：本 spec 先行；技能项目另立 spec，在其后。

## 3. 用户故事

### 注册与登录
1. 作为新用户，我可以访问 `/register` 填写昵称+密码提交，成功后自动登录进入控制台。
2. 作为新用户，若昵称已被占用，我收到明确的冲突提示（actor.nickname 加 UNIQUE）。
3. 作为实例的**第一个**注册者，我的账号自动获得创始人标记，且此前所有无主数据
   （workspace/会话/应用/全局 llm 配置）归属变为我的账号，界面上无缝无感。
4. 作为已注册用户，我可以在 `/login` 用昵称+密码登录，获得 HttpOnly 会话 cookie；
   登录成功时轮换 actor.token（旧设备会话随之失效——本地单人场景可接受，见 §6）。
5. 作为已登录用户，我可以登出（清 cookie + 轮换 token 使凭据作废）。
6. 作为访问者，未登录时所有 `/api/*` 业务接口返回 401，前端拦截后跳转登录页；
   白名单仅 `POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/me`。
7. 作为忘记密码的用户，我可以用命令行脚本按昵称重置密码（无邮件找回，本地场景够用）。

### 隔离
8. 作为用户 A，我在列表接口（workspaces/apps/sessions/config）永远看不到用户 B 的资源；
   直接按 id 访问 B 的资源返回 404（不区分 403，避免探测 id 存在性）。
9. 作为用户 A，我可以把与用户 B 相同的路径注册为自己的 workspace（root_path 唯一约束
   改为 `(owner_user_id, root_path)` 联合唯一）——两个账号在同一块磁盘上各自干活。
10. 作为用户，我看到的模型配置（llm.base_url / api_key / model 等）是我自己的；
    新用户首次进入设置页时是空配置，需自行填写。
11. 作为用户，我的会话/消息/文件变更审计通过所属 workspace 传递隔离，不单独授权。

### WebSocket
12. 作为已登录用户，聊天流 `ws.py` 与部署流 `deploy_ws.py` 照常工作（浏览器同源自动带
    cookie）；未登录建立 WS 连接时，服务端以自定义 close code（4401）拒绝。

### 前端
13. 作为用户，我在侧栏看到当前账号昵称，可登出；应用商店沿用现有 actor 识别，
    登录后自动等于当前账号（不再有"留下名字"弹层）。
14. 作为零经验用户，注册页/登录页只要求昵称+密码两个字段，无邮箱无验证码。

## 4. 已定决策（勿翻案）

| 决策 | 内容 |
|---|---|
| 部署模型 | 本地单人实例+账号，非共享多租户服务器（登录体系按隔离设计，为未来上云预留） |
| 账号验证 | 本地账号体系，不建云端认证、不做 OAuth |
| 表结构 | actor 升级为用户，不新建 user 表，市场外键不动 |
| 隔离程度 | 多账号完全隔离；越权统一 404 |
| 存量数据 | 创始人注册时单事务承接全部无主数据 |
| 注册策略 | 开放注册，无审批 |
| 顺序 | 先登录项目，后技能项目（两 spec 两计划） |

## 5. 实现要点

- **迁移 SQL**（规则要求版本化）：`changelog/V20260925_01_actor_auth.sql`（actor 加
  `password_hash`/`is_founder`、nickname 加 UNIQUE）与 `V20260925_02_owner_columns.sql`
  （归属列与约束改造，见下）。两步走原则：先加列/新约束，旧约束确认无依赖后再删。
- [归属表清单] workspace、app_project、**model_config（模型档案库，含 api_key，实现时补遗）**
  加 `owner_user_id`；app_config 主键改 `(owner_user_id, key)`。另：model_config 现有的
  `uk_model_default` 全局唯一默认索引（同库仅一行 is_default）与多账号冲突，改为按用户
  部分唯一 `(owner_user_id, is_default) WHERE is_default`；存量无主 model_config 行并入创始人。
- **认证会话**：沿用 cookie 名 `zc_actor`（现有 store 代码已在发），语义从"设备标识"
  升级为"会话凭据"；`by_token()` 改造为认证入口（命中即续期的行为保留）。
- **FastAPI 收口**：新增 `app/api/auth.py` + 依赖注入 `current_actor`；`request.state.actor`
  供业务层取用；所有业务 router 查询强制 `WHERE owner_user_id = :uid`。
- **bcrypt**：requirements 新增 `bcrypt`（直接用库，不引入 passlib）。
- **identity.py 注释翻转**：删除"不得演化成授权依据"的旧约束声明，指向 ADR-0003。
- **创始人承接**：注册服务内判断"无账号存在"→ 置 `is_founder`，同事务把
  `owner_user_id IS NULL` 的行与全局 llm 配置行划归该账号。

## 6. 测试决策

- 最高接缝 = **HTTP API**（沿用现有 tests/ 的 TestClient/httpx 模式），不做单元级 auth 内幕测试。
- 关键用例：注册→登录→me 闭环；双账号交叉访问全部资源接口得 401/404；同路径两账号
  各建 workspace；创始人承接存量；WS 未登录 4401；登出后旧 cookie 失效。
- "完成"= 新增隔离测试全绿 + 现有全量测试（改造后）全绿 + 前端手动过一遍冒烟。

## 7. 明确不做

- 技能功能本体（下一个 spec：用户技能库、SKILL.md 安装器、prompt 注入）。
- 多设备并发会话（token 轮换意味着新登录踢掉旧会话，v1 接受）。
- 邮箱验证、验证码、找回密码邮件流、登录限流/锁定、密码复杂度策略。
- 角色权限系统（只有 `is_founder` 布尔标记，v1 创始人无特权 UI，仅承接数据这一含义）。
- 云端同步、跨机器账号漫游。
- 应用商店既有流程的行为变更（仅身份来源从"弹层留名"变为"登录态"）。

## 8. 补充说明

- 本改造是技能项目"受控读面"（Agent 读 `users/<uid>/skills/`，见 ADR-0004）的依赖：
  技能项目开工前，`users/<uid>/` 的归属语义必须已经稳定。
- 控制台继续 localhost 直连不走网关，此决策不变（CONTEXT.md「控制台」词条）。
- `zc_actor` cookie 加 `SameSite=Lax` + HttpOnly；本地 http 下不设 Secure，上云时需随
  部署方案一并处理（届时另立 ADR）。
