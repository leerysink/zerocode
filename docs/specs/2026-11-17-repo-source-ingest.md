# 仓库源码摄入规格说明（Gitee URL → 应用内容快照）

- 日期：2026-11-17
- 上游：`docs/superpowers/plans/2026-11-17-repo-source-ingest.md` —— 该计划 §1「缺口分析」是本文的规格来源，
  四条通道的实测证据表（HTML 页 / API 元数据 / 递归文件树 / raw 正文，全部 200）也在该节
- 决策：[ADR-0051 仓库源码摄入的边界](../adr/0051-repo-source-ingest-boundary.md)
- 术语：`CONTEXT.md` 的「仓库源码摄入（repo source ingest）」一节
- 先例：[2026-09-26 用户技能](2026-09-26-user-skills.md) §5 的出站安全预检 —— 判据与快照通道共用同一份

**状态**：Accepted（随 2026-11-17 实施计划交付）。§9 的端到端验收数值由计划 Task 9 实测后回填，
本文只写验收判据，不预填任何未测得的数字。

## 1. 问题陈述

用户在建应用时粘一个**公开 Gitee 仓库地址**，期望平台读它、往下钻取、把仓库里的文本内容烘成
应用素材，并且发布后的站点自己就能读这份内容做目录与详情页。

平台侧的四段缺口（计划 §1 的结论，逐条对应本规格的交付）：

| # | 缺口 | 处置 |
|---|---|---|
| 1 | 建应用入口没有仓库 URL 位置（`AppIn` 只有 `name/slug/scale/stack`） | §5.1 的 `source` 字段 |
| 2 | 抓取与钻取范式只服务 GitHub 技能通道 | §2.1 的 `app/source/` 包（SSRF 判据抽成共享模块） |
| 3 | 远端内容进不了工作区（铺源只有模板渲染） | §5.3，走 `create_app_project` 既有的 `materialize` 接缝 |
| 4 | 生成站点读不到这份内容 | §2.2 的落点 `frontend/public/source/`，Vite 原样直出 |

匿名 API 配额实测 60 次/小时（计划 §1），这决定了 §2.2 的抓取形状：**一次摄入的 API 调用数不随文件数
增长**（默认两次，含斜杠分支的回退会把树请求重发一次，上限三次），正文一律走 raw 直链，不吃配额。

## 2. 方案概述

### 2.1 模块划分

| 文件 | 职责 | 出网 |
|---|---|---|
| `app/source/guard.py` | 出站判据唯一真值：URL 形态预检、SSRF 网段判定、流式限量下载、逐跳重定向预检 | 是（`fetch_limited`） |
| `app/source/hosting.py` | 托管站端点表 + `parse_repo_url` URL→`RepoRef` + `unsafe_path_segment` 路径形态闸（纯函数） | 否 |
| `app/source/ingest.py` | `fetch_repo_snapshot`：全树一次列路径 → 白名单筛选 → 逐文件 raw → 返回内存载荷 | 是 |
| `app/source/materialize.py` | `compose_with_source` / `write_snapshot` / `append_readme_provenance`：载荷落盘 + README 溯源段 | 否 |
| `app/services/app_service.py` | `create_app_project` 增 `source` 参数，把快照组合进既有铺源钩子 | 否 |
| `app/api/apps.py` | `POST /api/apps` 增 `source` 子模型；**抓取先于建应用**，失败即 400 | 经 ingest |
| `frontend/src/utils/appSource.js` | 向导载荷组装（空值不发键、超长前端先拦） | — |
| `frontend/src/views/AppsView.vue` | 新建向导第三步的「仓库地址 / 子目录」两个输入框 | — |

技能安装通道（`app/skills/installer.py`）改为调用同一个 `guard`，自身的 GitHub-only 范围、错误文案
与协议一律未变（ADR-0051 明确记录这条边界，免得与 `2026-09-26-user-skills.md` §7 看起来互相打脸）。

### 2.2 「往下钻取」是哪两级，不是什么

钻取只有两级，且**没有一层是浏览器里的爬虫**：

1. **服务端一次递归全树**：`GET /api/v5/repos/{owner}/{repo}/git/trees/{ref}?recursive=1`
   （`hosting.api_tree_url`）一次调用拿到整个仓库的路径清单，这就是「往下钻」的入口 ——
   平台**不逐层点目录**、不递归发多次树请求。
2. **站点侧按 manifest 逐文件 fetch**：快照里的 `manifest.json` 带 `files[]`（路径、字节数、首个
   标题、所在目录），生成的页面据此渲染目录树，点击时 fetch `/source/files/<仓库原路径>` 取正文。

正文走 raw 直链（`hosting.raw_file_url`），因此一次摄入的 **API 调用数与文件数无关**：默认两次 ——
仓库元数据取默认分支 + 递归全树；URL 里已带 `/tree/<ref>` 时分支不必再问，只剩一次树调用。
唯一会多出来的是**含斜杠分支的那次回退**（US-09）：第一次树调用 404 且本次带着生效子目录时，
`fetch_repo_snapshot` 把「分支/子目录」并成一个 ref、**把树请求重发一次**，所以「仓库根 URL +
显式 `subpath` 参数 + 默认分支的树 404」这条组合最多三次 API 调用。重发次数是固定的（树调用最多两次），
与文件数、目录深度、仓库大小都无关 —— 「不随文件数增长」这条配额口径不因它而变。

不是钻取的东西：运行时代理 Gitee、给平台 Agent 的任意 URL 抓取工具、把仓库当数据源实时同步。
快照是**构建期**产物，站点读的是自己工作区里的静态文件。

### 2.3 载荷契约 `zc-source/1`

`fetch_repo_snapshot` 返回 `{"manifest": {...}, "files": {仓库路径: bytes}}`；落盘后
`manifest.json` 的字段（`ensure_ascii=False` + UTF-8 + `newline=""`，中文原样入库）：

| 字段 | 含义 | 口径 |
|---|---|---|
| `schema` | 契约名 | 固定 `zc-source/1`（与 `app.json` 的 `zc-app/1` 同族命名） |
| `host` / `owner` / `repo` | 仓库坐标 | `host` 恒为 `gitee`；`app_project.origin` 由它拼成 `repo:gitee` |
| `ref` | 实际抓取的 ref | 用户没点分支时取 `default_branch`；含斜杠分支名的 404 回退命中后，这里是「分支/子目录」合并串 |
| `sourceUrl` | 用户粘的原始 URL 全文 | URL 只留在 manifest，不进数据库列 |
| `fetchedAt` | 抓取时刻 | UTC ISO-8601，秒级精度（微秒归零） |
| `subpath` | 生效的子目录，无则 `null` | 子目录被并进 `ref` 时同样归 `null`，避免同一个目录在两个字段里各说一遍 |
| `truncated` | **恒为 `false`** | 树自报截断时当场抛错中止（§5.4），不存在「截断但仍建成了应用」这种部分成功 |
| `counts` | `listed` / `snapshotted` / `skipped` 三个数 | 见下方警示 |
| `files[]` | `path` / `bytes` / `title` / `dir`，按 `path` 排序 | `title` 是正文第一个 ATX 标题（跳过代码围栏内的伪标题），没有则 `null` |
| `skipped[]` | `{path, reason}`，按 `path` 排序 | 只写前 200 条（`fetch_repo_snapshot` 末尾的字面量截断，无命名常量）；`counts.skipped` 仍是**全量**条数 |

**`counts` 的三个数不构成相加关系**，理由要说准：**计数域不重合**，不是「有些被拒的不计数」。
`listed` 数的是递归树返回的**全部条目**，含目录段（`type=tree`）与本次范围外的 blob。判定顺序是先判
形态/白名单/lockfile/声明体积，**后**判 `subpath` 范围（`ingest.fetch_repo_snapshot` 的循环就是这个次序），
所以 `skipped` 收的是「被某条规则拒掉」的那些，**与它在不在范围内无关**：范围外但不合规的条目照样进
`skipped`（实测 `subpath=docs` 时，`frontend/package-lock.json` 计 `lockfile`、`node_modules/evil/index.md`
计 `ignored-dir`，两者都在 `docs/` 之外）。反过来，**范围外且形态合规**的条目两头都不记——它从来不在
本次范围内，既不进快照也不计为跳过。因此本文与 README 溯源段一律按 `listed ≠ snapshotted + skipped`
的口径**只并列三个数、不写成相加**：相加式至多偶然成立（只有全树没有目录段、也没有「范围外且合规」的
blob 时才碰巧相等），把它当恒等式读就是给一条不存在的不变量背书。

`reason` 取值清单（`ingest.is_eligible` + 下载期降级，取值集合不随 Task 10 变化）：`unsafe-path`
（越界、前导 `/`、反斜杠，以及带内部点段的树路径如 `docs/../x.md`——落盘会归一、manifest 不会，
出现即拒）、`ignored-dir`、`lockfile`、`not-text`、`too-large`（声明体积超限或实际下载超限）、
`not-found`（raw 返回 404/410）、`server-error`（raw 返回 5xx）。单文件取不到不否决整次摄入；
安全类拒绝与权限/配额类错误原样上报，不用「跳过」掩盖。

## 3. 用户故事

### 建应用与落盘

- **US-01** 用户在向导第三步（选技术栈那一步）填 Gitee 公开仓库地址并创建应用，201 成功后工作区里有
  `frontend/public/source/manifest.json` 与 `frontend/public/source/files/<仓库原路径>` 逐篇正文，
  `README.md` **追加**一段「仓库源码快照」溯源说明（模板那份 README 原文保留）。
- **US-02** 应用栈不限：五个已交付栈（`vue3-fastapi`、`vue3-node`、`static`、`vue3-springboot`、
  `vue3-springboot-mobile`）的前端都是 Vite 工程，`frontend/public/` 是默认静态目录，快照落点与栈无关，
  创建链路不因栈分支（有 `vue3-springboot` + `source` 的端到端测试钉住）。
- **US-03** 不填仓库地址的创建请求行为**逐字节不变**：不发任何网络请求、不落快照目录、
  `app_project.origin` 保持 `NULL`、响应形状一字不多。
- **US-04** 地址框留空或只有空白字符（含制表符/换行）视同「没填」：既不发请求，也不报错拦住正常创建。

### 收窄范围（subpath 三态）

- **US-05** 用户填「子目录 = docs」→ 只有 `docs/` 下的正文进快照，`manifest.subpath` 为 `docs`，
  `counts.listed` 仍是**整棵递归树**的条目数（不是该目录下的条数）。目录名打错了（`doc`）不产出空站点：
  范围非空却挑不出一篇正文即一次 **400**，文案给出下一步（§5.2、§5.4）。
- **US-06** 用户粘 `…/tree/master/docs` 且**不填**子目录 → 沿用地址携带的目录，效果同 US-05。
- **US-07** 用户粘 `…/tree/master/docs` 且 API 请求显式带 `subpath: ""` → 意图是「就要仓库根」，
  全域入快照、`manifest.subpath` 为 `null`。显式参数优先于 URL 带的路径，这条优先级在 HTTP 层
  不许折叠（§5.2）。抓取侧先把 `subpath` 的整体空白 strip 掉，所以**只有空白字符的值同归本态**——
  那是用户把输入框擦干净了，不是一个名叫空格的目录（原来按字面筛会静默零命中）。
- **US-08** **向导表达不出 US-07**：输入框初值就是空串，若把空串原样发出去，粘目录型 URL 却不填框的人
  会被静默切成「仓库根全域」，与他刚粘的意图相反。所以 `sourcePayload` 在子目录为空白时**不发** `subpath`
  键（等价于 US-06 的「沿用地址携带的目录」）。这是有意的收窄，不是漏实现（§4 D3）。
- **US-09** 用户粘含斜杠的分支名（`…/tree/feature/x`）：URL 里天然是歧义的，平台先按「`feature` 是分支、
  `x` 是子目录」查树；404 时把两者并成 `feature/x` 当 ref 再试一次。命中后 `manifest.ref` 就是
  `feature/x`、`subpath` 归 `null`（同一个目录不在两个字段里各说一遍）；两次都 404 才报「分支或路径不存在」。

### 失败与零副作用

- **US-10** 抓取失败（配额用尽、私有仓、分支或路径不存在、非支持站点、超预算、URL 指向内网、
  显式 `subpath` 零命中）→
  `POST /api/apps` 返回 **400 + 一句中文**（文案见 §5.4），并且**不创建应用、不分配端口、不建目录、
  不发一个 raw 请求**：抓取排在建应用之前，零副作用是顺序给的保证，不靠回滚兜。
- **US-11** 快照落盘阶段失败（远端路径形态非法）→ 仍是一次 400，且 `create_app_project` 的既有回滚
  把已铺的模板树整个删掉，不留半棵树。这一支今天没有入口，但**别读成「抓取侧已经判过同一条」**（§4 D8）：
  `ingest.is_eligible` 与 `materialize._safe_rel` 只有前两段判据逐字相同（空串 / 前导 `/` / 反斜杠，
  以及归一后的 `..` 前缀与 `.`），落盘侧多一个 `posixpath.isabs(归一后)`；而真正拦住 Windows 盘符形态
  （`C:/x.md`）的是 `write_snapshot` 写盘前的 `relative_to` 复核 —— 抓取侧没这道，`is_eligible` 对盘符
  是**放行**的（posix 语义既看不出它绝对、也看不出它越界）。今天走不到，只是因为带这种形态的 blob 在
  raw 那一步换不到正文（`hosting.raw_file_url` 里的 `quote(path)` 把冒号写成 `C%3A`，远端 404 →
  降级成 `not-found`，进不了载荷），所以它是**侥幸不可达**（取决于远端真的不回内容）、不是被上游挡死。
  留着它兜的正是「接缝不校验载荷形状」——载荷的键直接来自远端树，写盘侧必须自己判（§5.4 末行、§6）。
  它不靠真实仓库也能测：`tests/test_source_materialize.py` 的
  `test_unsafe_remote_path_is_rejected_not_written` 按平台追加盘符形态，
  `test_absolute_path_cannot_land_outside_snapshot_files_dir` 是跨平台的包含性证明。

### 发布后的站点

- **US-12** 站点里 `fetch('/source/manifest.json')` 与 `fetch('/source/files/<路径>')` 在 dev 预览与
  发布后的正式地址都取得到 —— 靠 Vite 把 `public/` 原样拷进 `dist`，不改 `vite.config`、不改
  `nginx.conf`、不给静态栈加 router。
- **US-13** AI 用既有工具读快照做页面：`read_file`/`list_dir`/`search` 就够（本票一个 agent 工具都不加），
  单文件上限 200 KB 正是为了让快照里每一篇都能被 `read_file` 一次读完。
- **US-14** 页面文案不得声称「正在读取 Gitee」——读的是构建期快照，仓库更新要重建应用（§8）。

## 4. 已定决策

- **D1** 抓取发生在 `POST /api/apps` 请求内、**同步**、排在落库之前（ADR-0051 决策 1）。
- **D2** 落点 `frontend/public/source/`，走 `create_app_project` 既有 `materialize` 接缝
  （`compose_with_source` 把「基础铺源 → 写快照 → 追加 README」串成一个闭包）。
- **D3** `subpath` 三态是契约（§5.2）；向导刻意不支持显式空串那一态。
- **D4** SSRF 判据下沉为共享模块 `app/source/guard.py`，技能通道改薄包装、文案与语义逐字不变；
  两条通道各自保留自己的预算数值（技能 1 MB / 10 MB / 600，快照 200 KB / 8 MB / 400），
  `guard.fetch_limited` 的 `max_bytes` 由调用方传入。
- **D5** 只接公开 Gitee 仓库，匿名抓取，不带 token；`gitee.com` 与 `www.gitee.com` 都认。
- **D6** 顺序不可调：`base_render → write_snapshot → append_readme_provenance`。快照写在渲染**之前**
  会被 scaffold 的占位符残留自检打成渲染残留（远端正文里带 `{{APP_NAME}}` 是常事），一次粘贴把创建弄成 500。
- **D7** `origin` 只写短标记 `repo:gitee`（列是 `VARCHAR(60)`），URL 全文留在 manifest —— 本票不改表结构、
  不加迁移。
- **D8** 远端路径在**落盘侧二次归一**（`materialize._safe_rel`）：抓取侧判的是「能不能拼进 URL」，
  落盘侧判的是「能不能拼进 `Path`」——Windows 上 `Path(dir) / "C:/x.md"` 会整段替换成绝对路径，
  只有写盘前的 `relative_to` 拦得住。两道闸口径不同，不是重复。
  补一句免得有人去数差集：`_safe_rel` 比 `is_eligible` 多出的那个 `posixpath.isabs` 分支今天判不到
  盘符形态（posix 的 `isabs` 只认前导 `/`，而那种串在它上面一行就已经被拒），落盘侧真正干活的仍是
  `write_snapshot` 那道 `relative_to` 复核（US-11）。

## 5. 接口契约

### 5.1 `POST /api/apps` 的 `source` 字段

请求体在原 `name/slug/scale/stack` 之外增可选字段：

```json
{"name": "…", "slug": "…", "scale": "small", "stack": "static",
 "source": {"url": "https://gitee.com/owner/repo/tree/master/docs", "subpath": "docs"}}
```

- `url`（必填，`str`）：上限 1000 字符（`guard.MAX_URL_LEN`，与 `user_skill.source_url` 列宽同源；
  前端 `appSource.MAX_SOURCE_URL` 同值，超长在前端先拦一次）。`source` 对象缺 `url` 由 schema 层
  直接 422，一次请求都不发。
- `subpath`（可选，`str | None`）：上限 500 字符（`ingest._SUBPATH_MAX`）。**归一后的生效子目录**
  （参数或 URL 携带，取先到的那个）也受这条上限约束 —— 只卡参数会漏掉 `/tree/<ref>/<超长子路径>`。
- 成功：`201` + 与不填 `source` 时同形状的应用对象（响应里**不**新增 `source` 字段）。
- 抓取失败：`400` + 一句中文（`detail` 是字符串，前端沿用创建失败的 `message.error`）。
- `app_project.origin` 落 `repo:gitee`；未填/填空格时保持 `NULL`。
- 测试注入点：`request.app.state.source_http_transport`（httpx `MockTransport`），生产不设置即走真实网络。

### 5.2 `subpath` 的三种意图（互不等价）

| 载荷 | 意图 | 生效范围 | `manifest.subpath` |
|---|---|---|---|
| `subpath` 键缺席 | 「沿用地址里带的目录」 | URL 有 `/tree/<ref>/<dir>` 就钻该目录，否则全域 | 该目录，或 `null` |
| `subpath: ""`（含只有空白的 `"   "`） | 「就要仓库根」 | **压过** URL 携带的目录，全域 | `null` |
| `subpath: "docs"` | 「钻这个目录」 | 该目录（含其子目录）；范围里零命中即 400，不产出空站点 | `docs`（去首尾空白与 `/` 后） |

优先级：显式参数 > URL 携带。HTTP 层原样转发、不许 `or None` 折叠（折叠一次就把「留空即整仓」
换成「只摄那个子目录」，与用户粘的 URL 相反）。向导只会产生第 1、3 两态，见 US-08。

一个边界事实：只有 `url` 在 HTTP 层 `strip()`，`subpath` 原样转发；抓取侧先 `.strip()` 再 `.strip("/")`。
所以 `subpath: "   "`（只有空白字符）归一成**空**，即「就要仓库根」那一路——那是用户把输入框擦干净了
留下的 UI 残留，不是一个名叫三个空格的目录名，按字面去筛只会静默零命中。前端 `sourcePayload` 先 trim
再判空（US-08），因此这个形态本来就只能由直接打 API 的客户端造出来，抓取侧仍按同一条口径归一。

两条已收口的判据（Task 10，都不是「当前实现如此、非契约意图」）：

- **显式范围零命中 = 400**：`subpath` 归一后非空、但范围里挑不出一篇正文（打错的目录名如 `doc`）时，
  `fetch_repo_snapshot` 抛「该目录下没有可摄入的文本」，`POST /api/apps` 回 400，不产出零正文的应用。
  静默 201 是把失败藏到用户打开页面那一刻，报错才是可执行的反馈。
  这一判**只在范围非空时成立**：仓库根零正文（全域输入撞上纯二进制仓库）仍是一次合法的空快照，不报错。
  子目录被并进 `ref` 的那一路（US-09）命中后 `subpath` 归 `null`，因而不再走这条判定——那一次树调用
  已经证明目录真实存在，目录里当真是零文本就是一次合法的空快照，不是打错名字。
- **带内部点段的树路径 = 拒收**：`is_eligible` 对 `docs/../oops.md` 这类串判 `unsafe-path`，
  不「归一后双存」——落盘按归一名写、`manifest.files[].path` 按原串存，站点照 manifest 去 fetch
  就撞 404，这种自相矛盾的载荷不是「形状怪一点」。git 树里造不出这种路径，出现即说明输入不可信。

### 5.3 落盘产物与读路径

```
<workspace>/frontend/public/source/manifest.json     索引（§2.3）
<workspace>/frontend/public/source/files/<仓库原路径>  正文原样字节，不改编码、不剥标题
<workspace>/README.md                                追加「## 仓库源码快照（<sourceUrl>）」一段
```

路径常量单一真源：`materialize.SOURCE_ROOT_PARTS` / `MANIFEST_REL` / `FILES_REL_PREFIX`，
测试的读侧也钉在常量上（`tests/test_apps_source_api.py` 取 `MANIFEST_REL` 与 `FILES_REL_PREFIX`；
`tests/test_source_seam.py` 里仍留着一处硬编码的快照正文路径，是已登记的瑕疵，不是另一条契约）。
README 缺文件时兜底写成一份只含溯源段的 README，不静默丢出处信息。

### 5.4 错误口径（文案节选自代码）

| 情形 | 判定位置 | 400 文案要点 |
|---|---|---|
| URL 非 http/https、超 1000 字符 | `guard.validate_http_url` | 「仅支持 http/https 直链」/「URL 超过 1000 字符上限」 |
| URL 主机是内网/保留地址、`localhost`、IP 混淆写法 | `guard.reject_internal_host` | 「不允许指向内网/保留地址」等 |
| 重定向落点越界或协议不允许 | `guard.fetch_limited` | 「重定向目标协议不允许」/内网文案 |
| 主机不是 Gitee | `hosting.parse_repo_url` 返 `None` | 「只支持公开托管仓库，当前接入：gitee.com」 |
| owner/仓库名/分支/子路径带 `.`、`..`、空段、反斜杠 | `hosting.unsafe_path_segment` | 「…形态非法：不允许 . / .. / 空路径段 / 反斜杠（问题段 …）」 |
| URL 形态既不是仓库根也不是 `/tree/<ref>…` | `hosting.parse_repo_url` | 「仅支持仓库根 URL 或 /tree/<ref>[/<subpath>] 形态」 |
| 仓库无默认分支 | `hosting.default_branch_of` | 「仓库没有默认分支，请用 /tree/<ref> 指定分支」 |
| 远端 401 | `ingest._remote_error` | 「仓库不可读（私有或需要授权），本能力只接公开仓库」 |
| 远端 403 | `ingest._remote_error` | 「Gitee API 配额已用尽或访问被拒（匿名 60 次/小时），请稍后重试」 |
| 树 404（含斜杠分支回退后仍 404） | `ingest.fetch_repo_snapshot` | 「获取仓库文件树失败：分支或路径不存在（HTTP 404）：`<最后一次请求的那个 ref>`」——回退跑过时这里就是「分支/子目录」合并串，不重插 `subpath`（US-09） |
| 树响应超 `TREE_MAX_BYTES` | `ingest._fetch_tree` | 「仓库文件树响应超过 8MB，请用 /tree/<ref>/<subpath> 或 subpath 参数收窄范围」 |
| 树自报 `truncated` | `ingest.fetch_repo_snapshot` | 「仓库文件树被截断（truncated），请…收窄范围」 |
| 声明体积预估超预算 / 下载中累计超预算 | 同上 | 同一个「文本总大小超过上限」，括号里分标「按文件树声明预估」/「下载中按实际字节累计」 |
| 候选文件数超 `FILE_COUNT_MAX` | 同上 | 「文件数超过上限: {N} > 400」 |
| 显式 `subpath` 归一后非空、范围里挑不出一篇正文（打错的目录名） | 同上（`picked` 为空且 `scoped` 非空） | 「该目录下没有可摄入的文本：subpath=…（范围里有文本正文吗？）」——全域零正文**不**走这条，仍是合法空快照（§5.2） |
| 网络层异常 | `ingest.fetch_repo_snapshot` | 「远端请求失败: {异常类型名}」 |
| 落盘形态非法（今天无已知入口；不可达的理由见 US-11，不是「抓取侧判过同一条」） | `materialize._safe_rel` 的越界判 + `write_snapshot` 写盘前的 `relative_to` 复核 | 「快照路径形态非法: …」/「快照路径越界: …」，由 `create_app_project` 包成「创建应用失败: …」，仍 400 并回滚 |

## 6. 边界与安全

**判据唯一真值 = `app/source/guard.py`**：SSRF 网段判定、URL 形态预检、流式边下边限长、逐跳重定向预检
（含跨主机落点摘掉 `authorization`）。技能安装与快照摄入共用这一份。域名**不做 DNS 解析后校验**
（防 rebinding）——这条边界沿用技能通道既定裁定（`2026-09-26-user-skills.md` §5 安全预检），
单机部署假定下成立；要收紧必须先另立 ADR，不许在守卫里顺手加解析。

**预算**（标 `guard.*` 的两行在 `app/source/guard.py` 顶部，其余命名常量都在 `app/source/ingest.py` 顶部；
最后两行是函数体内的字面量，**没有命名常量**）：

| 常量 | 值 | 作用 |
|---|---|---|
| `MAX_FILE_BYTES` | `200 * 1024` | 单文件正文上限，对齐 `app/agent/tools.py` 的 `MAX_READ_BYTES`，保证快照每篇都能被 `read_file` 一次读完 |
| `TOTAL_MAX_BYTES` | `8 * 1024 * 1024` | 快照正文总量；比技能通道的 10 MB 保守，因为快照要随 `vite build` 进应用镜像 |
| `FILE_COUNT_MAX` | `400` | 候选文件数上限，超了就是整仓 mirror 而不是素材快照 |
| `TREE_MAX_BYTES` | `8 * 1024 * 1024` | 递归树 JSON 响应的体积上限 |
| `_SUBPATH_MAX` | `500` | 生效子目录长度上限（§5.1） |
| `TEXT_EXTS` | 10 个扩展名 | 白名单：`.md .mdx .txt .rst .json .yaml .yml .csv .toml .ini` |
| `LOCKFILE_NAMES` | 12 个文件名 | 一律不收（`package-lock.json`…`go.sum`、`Cargo.lock` 等） |
| `guard.MAX_URL_LEN` | `1000` | URL 长度上限（两通道同源） |
| `guard.MAX_REDIRECTS` | `5` | 手动跟随的重定向跳数上限 |
| `manifest.skipped` 截断 | `200` 条 | 写在 `fetch_repo_snapshot` 末尾的字面量，**无命名常量** |
| 单次 HTTP 超时 | `20.0` 秒 | 写在 `ingest._client` 的字面量，**无命名常量** |

**跳过目录**：路径任一段（含文件名本身）命中 `app/workspace_ignore.IGNORE_DIRS`
（`.git`、`node_modules`、`__pycache__`、`dist`、`.venv`、`build`）即不收 —— 与文件树/检索工具同一张名单。

**不出网的部分一律前置**：URL 预检、host 支持判定、ref/subpath 形态闸、树元数据的声明体积预估都排在
第一个字节下载之前，所以「失败零副作用」是结构保证，不是 `try/finally` 兜出来的。

**不可信输入的分层归一**：默认分支名（远端给的）与 URL 里的 ref/子路径（用户粘的）都在拼 URL 之前过
`hosting.unsafe_path_segment`；递归树里的 blob 路径则由 `ingest.is_eligible` 判，它比落盘侧少一个
`posixpath.isabs` 分支、也看不见 `Path` 拼接后的真实落点，所以写盘前还要再过 `materialize._safe_rel`
与 `write_snapshot` 的 `relative_to` 复核（§4 D8、US-11）。`fetchedAt` 用 UTC，避免宿主机时区进契约。

**已知代价**：一次请求内最多 `FILE_COUNT_MAX` 次**串行** raw 下载（8 MB 预算封顶）。真实仓库的耗时是
§9 的验收项，不在本文预先承诺数字；若实测明显偏慢，再议是否改成「202 受理 + 后台执行」，
那是一次独立的架构决策（ADR-0051 后果）。

## 7. 测试决策

**「完成」长什么样**：下列定向文件全绿 + 技能通道两份回归文件（`tests/test_skills_api.py`、
`tests/test_skills_github_api.py`）逐字不变地全绿 + 前端 `appSource.test.js` 与 `AppsView.test.js`
的「AppsView 向导仓库地址接线」用例全绿。全量套件按仓库纪律**不顺手跑**（会拆掉本机在跑的预览栈）。

- `tests/test_source_guard.py`：SSRF 判定（`localhost` / `a.localhost` / `10.0.0.5` /
  `169.254.169.254` 与 `127.0.1` / `2130706433` / `0x7f000001` 这些混淆写法）、URL 长度上限、
  流式限量的两条出口（声明 `content-length` 与实际字节）、逐跳重定向预检、跨主机落点摘凭证、
  「守卫与 installer 的包装对同一批输入判定一致」。
- `tests/test_source_hosting.py`：`parse_repo_url` 的接受/拒绝形态（仓库根、`/tree/<ref>`、
  `/tree/<ref>/<subpath>`、`.git` 后缀、SKILL.md 直链让路）与端点拼串，含形态闸的每个坏段。
- `tests/test_source_ingest.py`：预算与白名单、`subpath` 三态优先级（含「只有空白的值归仓库根」）、
  显式范围零命中报错（不产出空站点）、含内部点段的树路径按 `unsafe-path` 拒收、含斜杠分支的 404 回退、
  声明体积预估排在单文件剔除之前、`counts` 三数关系、`truncated` 恒 `false`、单文件级降级
  （`not-found`/`server-error`/`too-large`）、URL 已带 ref 时不再问默认分支（配额那条）。
- `tests/test_source_materialize.py`：落点常量、manifest 的 UTF-8 + LF 字节（中文夹具 + 正向字节断言，
  专门用来把 `ensure_ascii=False` 钉住）、越界/绝对/反斜杠路径在预检阶段被拒且零写入（盘符形态按平台
  追加，跨平台那条是 `test_absolute_path_cannot_land_outside_snapshot_files_dir`，US-11）、
  README 追加不覆盖、快照正文里的 `{{...}}` 不触发渲染自检。
- `tests/test_source_seam.py`：`create_app_project(source=…)` 与商店 `materialize` 正交组合、
  快照失败回滚后不留目录、不带 `source` 时与今天逐字节一致。
- `tests/test_apps_source_api.py`：抓取先于建应用的零副作用、403/401/非 Gitee/内网 URL/预估超预算/
  拼错目录名（零命中）这六条 400 路径、`source={}` 的 422、空白 url 不发请求、`subpath` 三态在 HTTP 层不折叠、
  `origin` 只在真摄入时写、响应不新增字段。
- 前端：`frontend/src/utils/appSource.test.js`（判定那一组）+ `AppsView.test.js` 里
  「AppsView 向导仓库地址接线」那组（接线：真进载荷、带子目录、超长拦下、重置不带旧地址、
  子目录文案不承诺「留空即全域」）。

用例条数以各文件里的 `def test_` / `it(` 为准（参数化会展开出更多用例），本文不锁计数。

变异测试用过的三处关键闸（去掉就必红的接线，不是纯函数覆盖）：HTTP 层的 `subpath` 原样转发、
向导 `v-model:value="form.sourceSubpath"` 的绑定（打错名会静默不发子目录）、`ensure_ascii=False`。

## 8. 明确不做

范围外条目与理由见 ADR-0051，逐条为：运行时代理 Gitee、给平台 Agent 的任意 URL 抓取工具、
私有仓库 token、GitHub/GitLab 快照通道、submodule 展开、限流退避重试、docs 站的 SSR/sitemap/robots、
自定义域名、应用卡片的来源角标。另有三条口径级别的「不做」：

- **不做「部分成功」**：树被截断、体积超预算、文件数超上限一律整次失败并给出下一步动作，
  不产出「抓到一半」的应用。
- **不把快照当镜像**：术语说「仓库快照」，「镜像」留给 Docker 产物；也不写进 `build-output/`
  （那是平台交付物目录，`scripts/build_output.py` 重生成，与应用产物无关）。
- **不改 agent 工具面**：`app/agent/registry.py` 的 `SPECS` 同名同序钉死，AI 读快照走既有
  `list_dir`/`read_file`/`search`。

## 9. 端到端验收（待 Task 9 实测回填）

本节是**验收判据**，数值一栏留空不是遗漏：Task 9 是 gated 的真副作用票（建应用、起 dev 预览、发布），
跑完才填，任何未测得的数字都不许出现在本文。

前置：`https://gitee.com/api/v5/repos/{owner}/{repo}` 与该仓的 `git/trees/{ref}?recursive=1` 都探到 200，
任一非 200 先停下报告托管站状态，不改端口、不换地址绕。

| 项 | 判据 | 实测 |
|---|---|---|
| 全域输入的 `counts` | `snapshotted > 0`，`listed ≥ snapshotted`，`truncated` 为 `false` | 待填 |
| 收窄输入（`subpath` 或目录型 URL）的 `counts` | `files[].path` 全在该目录下，`manifest.subpath` 等于该目录 | 待填 |
| 一次摄入的耗时 | 记录墙钟秒数并与 `FILE_COUNT_MAX` 对照，判断是否需要改成 202 后台任务；超出创建请求可用等待时间即判不通过并上报 | 待填 |
| 工作区落盘 | `frontend/public/source/manifest.json` 与 `files/` 在位、`README.md` 含溯源段、`app.json` 的 `stack` 正确 | 待填 |
| dev 侧读路径 | `http://localhost:<DEV_PORT>/source/manifest.json` 与任一篇 `/source/files/<路径>` 均 200，正文是真内容 | 待填 |
| prod 侧读路径 | 发布后经网关域名取同样两个地址均 200（验的是快照真随 `vite build` 进了镜像） | 待填 |
| 页面钻取 | AI 按 `files[].dir` 分组、`files[].title` 取标题做出浏览页，点两篇不同文档内容真的切换 | 待填 |
| 零副作用反证 | 故意填一个不存在的分支：创建返回 400，应用列表与工作区都不见该 slug | 待填 |
| 拼错目录名的 400 文案 | 拿真实仓库把子目录打错一个字母（`docs` → `doc`）再创建一次：返回 400 而不是 201 空站点，且这句文案在向导的报错位**读得通**（能看出是目录名不对、下一步该改成什么），不泄漏内部字段名 | 待填 |

结论回写本文本节并提交，演示应用按用户口径保留为夹具。
