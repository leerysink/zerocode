# 0051 仓库源码摄入的边界：构建期烘快照，不开放运行时出网

- 状态：Accepted
- 日期：2026-11-17
- 规格：[docs/specs/2026-11-17-repo-source-ingest.md](../specs/2026-11-17-repo-source-ingest.md)
- 关联：ADR-0031（Agent 侧不接收任意 URL，目标由后端解析后传入；ADR-0037/0038 只是它的会话化修订，
  不改出网判据）、ADR-0047（`vue3-node` / `static` 栈交付后，五个栈的前端都是 Vite 工程，
  `public/` 原样直出这条才成立）、`docs/specs/2026-09-26-user-skills.md` §5/§7（同一份出站判据的来处）

## 背景

用户要「给一个 Gitee 仓库地址就生成官网，站点能读这份内容并往下钻取」。

可行性不是问题：公开 Gitee 仓库的四条通道匿名实测全 200（HTML 页 / API 元数据 / 递归文件树 / raw 正文，
证据表记在实施计划 §1），本票只用后三条 —— HTML 页是前端渲染的，正文不全。容器出网事实上也没被禁
（应用栈用 `default` + `zcnet` 两张网络，没有 `internal:`，也没有 iptables 侧收口；生成的站点不设 CSP）。
所以真问题不是「通不通」，而是**把出网开在哪一层**：

- 开在**运行时**（站点或后端容器反代 Gitee）：等于给每个发布出去的应用一条对外 HTTP 出口，
  站点可用性绑上第三方托管站，且这套出口一旦开出来就不容易只留给它一个用途。
- 开在**平台 Agent 工具面**（新增「抓任意 URL」工具）：与 ADR-0031 立的规矩相反 —— 那条的要点正是
  「模型不能指定要访问哪里，目标由后端从会话身份解析」。
- 开在**构建期**（平台在受控请求内抓一次、把内容落成工作区静态文件）：出网只发生在平台自己的
  一次请求里，应用与站点从此不再碰外网。

## 决策

1. **只在构建期抓**：摄入发生在 `POST /api/apps` 落库之前、请求内同步执行，产物是工作区里的静态快照。
   运行时不代理 Gitee，Agent 也不获得任何抓取工具。顺序本身就是「失败零副作用」的实现——抓取失败时
   端口未分配、目录未建、库未供给，不依赖回滚。
   落地：`app/api/apps.py::create_app`（抓完才调 `create_app_project`）、`app/source/ingest.py::fetch_repo_snapshot`。
2. **落点 `frontend/public/source/`**：借 Vite 默认 `publicDir` 的原样拷贝特性直出 `/source/**`。
   不改 `vite.config`、不改 `nginx.conf`、不给静态栈加 router，dev 预览与发布后的站点走同一条读路径。
   代价是快照会随 `vite build` 进镜像，所以总量预算比技能通道更保守（8 MB）。
   落地：`app/source/materialize.py`（`SOURCE_ROOT_PARTS` / `write_snapshot`）。
3. **走 `create_app_project` 既有的 `materialize` 接缝**（商店安装用的同一条缝，
   `materialize.compose_with_source` 把「基础铺源 → 写快照 → 追加 README」串成一个闭包），
   不新增第二条铺源路径。落盘排在模板渲染**之后**：scaffold 的占位符残留自检扫整棵产物树，
   而仓库正文里带 `{{APP_NAME}}` 是常事。
   落地：`app/services/app_service.py::create_app_project` 的 `source` 参数。
4. **判据下沉为共享模块** `app/source/guard.py`（SSRF 网段判定、URL 形态预检、流式限量、逐跳重定向预检），
   技能安装通道 `app/skills/installer.py` 改薄包装，**错误文案与语义逐字不变**；两条通道各自保留自己的
   预算数值（技能 1 MB/10 MB/600，快照 200 KB/8 MB/400），`max_bytes` 由调用方传入。
   不做 DNS 解析后校验，沿用技能通道既有裁定（`2026-09-26-user-skills.md` §5）在单机部署假定下的边界。
   落地：`app/source/guard.py`（`validate_http_url` 内含 `reject_internal_host`、`fetch_limited`、
   `GuardError`/`RefMissing`）↔ 技能侧 `installer._validate_http_url` 与 `installer._get_limited` 两个薄包装。
5. **只接公开 Gitee 仓库，匿名抓取**：`gitee.com` 与 `www.gitee.com` 都认，`owner/repo` 之外的
   URL 形态只接受仓库根与 `/tree/<ref>[/<subpath>]`。以下一律不在本票范围，谁在评审里提出来就指回本 ADR：
   **运行时代理 Gitee**、**Agent 侧任意 URL 抓取工具**、**私有仓库 token**、
   **GitHub/GitLab 快照通道**、**submodule 展开**、**限流退避重试**、
   **docs 站的 SSR / sitemap / robots**、**自定义域名**、**应用卡片的来源角标**。
   落地：`app/source/hosting.py`（`_GITEE_HOSTS` / `parse_repo_url` / 三个端点拼串，全程不带鉴权头）；
   用户侧入口是 `frontend/src/utils/appSource.js` + `AppsView.vue` 向导第三步的两个输入框。

## 与技能通道那份「明确不做」不冲突

`docs/specs/2026-09-26-user-skills.md` §7 有一条「Gitee/GitLab 等非 GitHub 托管的仓库扫描」不做。
那条**管的是技能安装通道**：技能仍只从 `installer.parse_github_url` 认出的 GitHub 仓库扫 `SKILL.md`，
本票一行都没动它，那条至今为真。本票新增的是**第二条通道**（应用内容快照），它认 Gitee、找的是文本正文
而不是 `SKILL.md`，两条通道的语义、白名单、落点、配额口径都各自独立。两份文档并存是刻意的，
读起来像互相打脸的地方以本节为准。

## 备选方案

- **运行时反代 Gitee（站点真「读」远端）**：内容永不过期，但给每个应用一条对外出口、把站点可用性
  绑在第三方上，且出口开出来就收不回去。被否决 —— 与本 ADR 的标题正好相反的那一侧。
- **抓取做成「202 受理 + 后台执行」**（与 dev 起栈、发布同形）：创建请求不被下载拖住，但要新引入
  「应用已建、快照还在跑」的中间态与失败回执，而本票要的恰恰是「摄入失败就没这个应用」。
  先取同步 + 零副作用，耗时实测超线再另立一票（见「后果」）。
- **给 Agent 加一个 `fetch_url` 工具**：让 AI 自己读仓库更灵活，但直接违反 ADR-0031 的「不接收任意 URL」，
  且把出网面从「平台的一次请求」扩到「模型可自由调用的工具」。被否决。
- **快照落 `docs/` 或后端静态目录**：要分别为它加 router 或改 nginx 配置，且发布链的收包/指纹规则
  得跟着改；`frontend/public/` 是已有先例（`vue3-springboot` 模板就带）且零配置。

## 后果

- **快照会变旧**：仓库更新不会自动流进已建应用，口径是「重建应用才更新」。README 的溯源段落
  是这条口径的载体，里面写明「这是构建期快照，不是实时代理」，并要求页面不得声称「正在读取 Gitee」。
- **配额口径**：匿名 API 60 次/小时，一次摄入默认发两次 API 调用（默认分支 + 递归全树），正文走 raw
  不占配额；含斜杠分支的 404 回退会把**树请求重发一次**（规格 §2.2、US-09），这条组合上限三次，
  仍是固定次数、与文件数无关。单机部署够用。多用户并发或私有仓需要 token 时另立一票。
- **创建请求内有串行下载**：最多 `FILE_COUNT_MAX`（400）次 raw 请求、8 MB 预算，全在一次请求里同步跑完。
  这是决策 1 换来的已知代价，真实仓库的耗时是规格 §9 的验收项，**没有预先承诺的数字**；
  若实测明显偏慢，回到「202 后台执行」那条备选重新决策，而不是在评审轮里顺手改架构。
- **技能安装仍是 GitHub-only**：`app/skills/installer.py` 的 `parse_github_url` 主机判定不动。
  两条通道合并会把技能侧的安全文案与候选清单协议一起拖进未知改动，收益不成比例。
- **`build-output/` 与本票无关**：那是平台交付物目录（`scripts/build_output.py` 重生成），不是应用产物，
  快照不落进去。
- **不改表结构**：`app_project.origin` 只多一个取值 `repo:gitee`（`VARCHAR(60)` 放短标记），
  URL 全文留在 manifest 里；卡片来源角标不做，`origin` 目前只是给运维/排查用的事实字段。
- **不可信输入的闸成对出现，且两侧不对称**：分支名与 URL 里的子路径先在拼 URL 前过
  `hosting.unsafe_path_segment`（防点段归一改写出同域另一条 API 路径），递归树给出的 blob 路径过
  `ingest.is_eligible`，写盘前再过 `materialize._safe_rel` 与 `write_snapshot` 的 `relative_to` 复核。
  覆盖关系不是包含的：`_safe_rel` 比 `is_eligible` 多一个 `posixpath.isabs(归一后)` 分支；而 Windows 上
  `Path(x) / "C:/…"` 那种整段替换目标的形态，两道**字符串**判都放过去（posix 语义既看不出它绝对、
  也看不出它越界），只有 `relative_to` 拦得住。把两道合并成一道，留下的正是这个缺口
  （规格 §4 D8、US-11）。
