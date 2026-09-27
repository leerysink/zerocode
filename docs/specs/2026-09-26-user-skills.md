# Spec：账户级技能库与 SKILL.md 安装器（user skills）

日期：2026-09-26 ｜ 状态：待评审
前置共识：ADR-0004（先登录后技能；技能账户级、存用户库、渐进披露）、ADR-0003（actor 即登录用户）
术语：见 `CONTEXT.md`（技能 skill、actor、控制台 console）
前置项目：登录/用户隔离（`docs/specs/2026-09-25-login-user-isolation.md`，已完成）

## 1. 问题陈述

平台 Agent 目前只有一份写死的 system prompt（`app/agent/prompts.py`），用户无法给
Agent 扩展指令能力。ADR-0004 已拍板：技能遵循 Anthropic Agent Skills 开放标准
（SKILL.md + YAML frontmatter），从 GitHub/URL 安装，归属用户账户，母本存平台数据区
`users/<uid>/skills/`，构建 system prompt 时注入已启用技能的 name+description 清单，
Agent 判断相关时再 read_file 读全文（渐进披露）。登录项目已交付归属维度
（workspace.owner_user_id 等），技能的挂载点已存在，本 spec 落地技能本体。

## 2. 方案概述

三个垂直部件，一条主链路（安装 → 落库落盘 → 注入 → Agent 按需读取）：

- **技能库（存储）**：
  - DB 新表 `user_skill`（按 owner_user_id 隔离），存技能元数据与状态；
  - 磁盘母本存平台数据区 `data/users/<uid>/skills/<slug>/`（新增 `ZEROCODE_DATA_DIR`
    环境变量可覆盖，默认仓库根 `./data`）。注意：`.gitignore` 当前**未**忽略 `data/`，
    需在实现首票的预重构里补上 `data/` 一条（与 SQLite/运行时产物同区、不入库）。
- **安装器（GET 拉取，不用 git）**：后端 httpx 直拉 GitHub raw / codeload 或任意
  SKILL.md URL，解析 frontmatter 后母本落盘 + 元数据落库。monorepo 用可选 `subpath`
  定位；装完即启用。同名冲突 slug 加后缀，不覆盖。
- **prompt 注入 + 受控读面（消费）**：`AgentRunner._build_messages` 按
  session → workspace.owner_user_id 反查用户，把**已启用**技能的
  name+description+目录绝对路径列表注入 system 尾部 `<available_skills>` 块；
  Agent 用既有 read_file 读全文——工具层为技能目录开一条绝对路径白名单读面。

前端新增 `/settings/skills` 管理页（列表、启用开关、删除、安装表单）。

## 3. 用户故事

### 安装
1. 作为用户，我粘贴一个 GitHub 仓库 URL（如 `https://github.com/owner/repo`），
   系统扫描仓库内的 SKILL.md；恰好一个则直接安装，多个则返回清单让我选（可用
   `subpath` 如 `skills/pdf` 精确定位），安装后技能出现在我的技能库且默认启用。
2. 作为用户，我粘贴一个直接指向 SKILL.md 的 raw URL，系统把该文件所在目录整包
   下载为技能母本（支撑 references 相对链接）。
3. 作为用户，frontmatter 缺少 `name` 或 `description` 时安装被拒，收到明确报错；
   超过大小限制（单文件 1MB / 单技能目录 10MB / 600 个文件）同样被拒。
4. 作为用户，我重复安装同名技能时，第二个以 `slug-2` 落库，两个独立共存，互不干扰。
5. 作为用户，GitHub 拉取失败（网络/404/无 SKILL.md）时得到含原因的报错，不留半成品
   （先落临时目录，全部成功后原子搬入）。

### 管理
6. 作为用户 A，我在技能列表/所有技能接口中永远看不到用户 B 的技能（沿用越权 404）。
7. 作为用户，我可以对单个技能启用/停用（per-skill 布尔开关，状态存库）。
8. 作为用户，我可以删除技能：DB 行与磁盘母本同事务语义删除（先删目录成功再删行）。
9. 作为用户，v1 没有"更新"按钮；想更新就重装一次（产生 `-2` slug）。

### Agent 消费
10. 作为已启用技能的消费者（Agent），我的 system prompt 尾部出现
    `<available_skills>` 块，含每个启用技能的 name、description 与目录绝对路径；
    停用的技能不出现在块中。
11. 作为 Agent，我用 read_file 传技能目录内的**绝对路径**能读到 SKILL.md 及
    文本类支持文件（references/assets）；读 `scripts/` 子目录、非文本扩展名、
    技能目录之外的绝对路径一律被工具层拒绝（工作区相对路径规则不变）。
12. 作为用户，工作区文件树里永远看不到技能文件——技能只活在平台数据区与
    Agent 上下文里（ADR-0004 后果条目）。

### 前端
13. 作为用户，我在设置页能看到「技能」入口，进入 `/settings/skills`：列表
    （名称/描述/来源/状态）、启用开关、删除按钮、顶部安装输入框（一个文本框，
    自动识别 GitHub 仓库 URL 或 SKILL.md URL；monorepo 时可填 subpath）。

## 4. 已定决策（勿翻案，溯源 ADR-0004）

| 决策 | 内容 |
|---|---|
| 标准 | Anthropic Agent Skills：SKILL.md + YAML frontmatter（name/description 必填） |
| 归属 | 用户账户级，非工作区级；工作区目录不出现技能文件 |
| 母本位置 | `data/users/<uid>/skills/<slug>/`（`ZEROCODE_DATA_DIR` 可覆盖） |
| 生效方式 | system prompt 注入 name+description+路径清单，Agent 按需 read_file 全文（渐进披露） |
| 启用粒度 | **per-skill enabled 布尔**（裁定细化：ADR-0004"用户级总开关"展开为每个技能各自的开关，不设全局 master switch——理由同 Claude Code，全局开关逃生口价值低且易误伤全部会话） |
| 安装通道 | 仅 HTTP GET（raw/codeload），不依赖本机 git 可执行 |
| 脚本执行 | v1 只消费指令性内容，Agent 无 shell 工具，技能内脚本不可执行（工具层同时拒 scripts/ 读取，双保险） |
| 名称来源 | frontmatter `name` 为准；slug 由 name 转 ASCII kebab-case，非 ASCII 字符剔除，空则回退 `skill`，冲突加 `-N` 后缀 |
| 数据形状 | `user_skill` 表 + 磁盘目录双轨：DB 是状态真源（enabled/元数据），磁盘是内容真源（母本） |

## 5. 实现要点

- **迁移 SQL**（规则要求版本化）：`changelog/V20260926_01_user_skill.sql`——
  `user_skill(id, owner_user_id REFERENCES actor, slug UNIQUE, name, description,
  source_type, source_url, subpath, skill_dir, enabled BOOL DEFAULT true,
  created_at, updated_at)`。
- **frontmatter 解析**：requirements 新增 `pyyaml`；解析 SKILL.md 首个 `---` 块，
  取 name/description（其余字段 v1 忽略但保留原文）。
- **安装器**（新模块 `app/skills/`：`installer.py` + `library.py`）：
  - GitHub：URL 解析出 `repo@ref`（默认 `HEAD`），用 codeload tarball/gzip 或
    git trees API + raw 内容拉取，按 `subpath`（可空）扫描 SKILL.md；
    可配 `ZEROCODE_GITHUB_TOKEN`（可选环境变量，缓解 rate limit），无 token 走匿名。
  - 直链：GET SKILL.md URL，同目录文件按 frontmatter 正文中出现的相对路径
    引用尽力拉取（失败不阻塞安装，SKILL.md 本体必须成功）。
  - 落盘走临时目录 → `os.replace` 原子搬入；大小/文件数超限即拒。
  - 安全预检（001 起各通道共用）：仅 http(s)；拒内网/保留地址与 IP 混淆写法（含
    302 重定向落点复查）；url/name 长度按 DDL 列宽（1000/200）先校验再入库；
    单文件超 1MB 在流式下载中即拒不先吃进内存。域名的 DNS 解析后校验
    （防 rebinding）为单机部署假定下的边界，不在 v1。
- **API**（新 router `app/api/skills.py`，prefix `/api/skills`，全部走 `current_actor`
  依赖，owner 过滤与越权 404 沿用登录项目模式）：
  - `GET /api/skills` 列表；`POST /api/skills/install`（body: url；subpath 随 002
      的 GitHub 通道兑现）；
    `PATCH /api/skills/{id}`（enabled 翻转）；`DELETE /api/skills/{id}`。
  - install 的 monorepo 多命中响应：`{"candidates": [{path, name?}...]}`（HTTP 200），
    前端据此二次提交带 subpath——不引入独立的 search 端点。
- **prompt 注入**：`prompts.py` 的 `build_system_prompt` 加形参
  `skills: list[dict] | None`，渲染 `<available_skills>` 块 + 一条规则说明
  （"技能是已验证的扩展指令，判断与任务相关时用 read_file 读该路径下 SKILL.md 全文再遵循"）；
  `loop.py` 在 `_build_messages` 内一处 join 取 owner_user_id：
  `chat_session → workspace.owner_user_id`，再查 `user_skill WHERE owner_user_id=:u AND enabled`。
- **受控读面**：`tools.py` 新增 `resolve_skill_path(root, abs_skills_dir, rel) -> Path`
  （模式仿 `app/deploy/guard.py`）：read_file 先走既有 `resolve_in_workspace`，
  ToolError 后回落尝试技能根白名单（技能根 = `{data_root}/users/{uid}/skills`，
  由 ToolContext 新增 `skills_root: Path | None` 字段传入）；命中后额外守卫：
  拒 `scripts/` 子树、拒非文本后缀（复用 `BINARY_EXTS` 反选 + 允许
  .md/.txt/.json/.csv/.yaml 白名单）、拒超过 MAX_READ_BYTES。写类工具（write/edit）
  **不**开放技能目录（技能母本对 Agent 只读）。
- **前端**：`router/index.js` 加 `/settings/skills` 懒加载路由，新建
  `views/SkillsView.vue`（沿用 SettingsView 的表单/卡片风格），`api/index.js` 加
  skills 端点封装，SideBar 设置区加入口；vitest 只测纯函数（slug 候选展示、
  URL 识别 helper 放 `utils/skillForm.js`），组件正确性走浏览器冒烟（项目惯例）。

## 6. 测试决策

- **最高接缝 = HTTP API**（沿用 tests/ 的 TestClient/httpx 模式）：
  - 安装→列表→停用→删除闭环（installer 的远端拉取用 `httpx.MockTransport` 注入，
    不打真网络）；双账号交叉访问 404；frontmatter 缺字段/超限拒绝；同名 `-2` slug。
  - 候选清单响应形状（多 SKILL.md 仓库）。
- **prompt 注入接缝**：`AgentRunner._build_messages` 已有 fake-llm 测试模式，
  断言 system 尾部 `<available_skills>` 块内容随 enabled 变化。
- **受控读面**：`execute_tool` 层单测——技能目录绝对路径可读、scripts/ 拒、
  目录外绝对路径拒、工作区相对路径行为不变。
- "完成" = 新增测试全绿 + 现有全量测试全绿 + 前端冒烟（装一个真实公开仓库技能，
  开会话说一句触发 Agent 读 SKILL.md 并遵循）。

## 7. 明确不做

- 技能市场/推荐榜单/浏览界面（"市场"是应用商店的概念；技能 v1 只有 URL 安装）。
- 版本检测与一键更新（含 upstream diff、changelog）。
- 脚本执行、subagent 化运行、`allowed-tools` 等 frontmatter 高级字段消费。
- 技能拷入工作区 / 随应用打包 / 跨用户共享或发布。
- 数据区文件的 OS 级隔离或加密（沿用本机单用户假设，DB 才是状态真源）。
- Gitee/GitLab 等非 GitHub 托管的仓库扫描（任意 SKILL.md 直链已覆盖大部分场景）。
- 细粒度每技能工具权限、按会话选技能（启用即全会话生效）。

## 8. 补充说明

- GitHub 匿名 raw/codeload 有 rate limit，故留 `ZEROCODE_GITHUB_TOKEN` 可选通道；
  国内网络直拉 GitHub 不稳定是已知事实，报错信息要把原因透出来方便排查
  （本机 Docker Hub 需加速源的教训不适用于纯 HTTP，但如实测 raw.githubusercontent
  不通，考虑镜像域名环境变量，不进 v1 默认）。
- `<available_skills>` 块与 `<current_todos>`、`<context_summary>` 同区（system 尾部），
  顺序：skills 在 todos 之前，避免压缩逻辑动快照区。
- 技能目录对 Agent 可见的是**绝对路径**，提示词里要写明"只读"；Agent 试图
  write_file 到技能目录会得到与今天越界同样的工具错误。
- ADR-0004 的"受控读面是登录项目完成后的第一个下游改动"由本 spec 兑现。
