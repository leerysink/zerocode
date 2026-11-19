# 应用商店：预览、发现体验与生命周期收口

- 日期：2026-11-19
- 状态：待实施（共识已达成，供 to-tickets 切分）
- 上游：grill-with-docs 共识（2026-11-19）、ADR-0057（商店在线试用复用作者 dev 预览栈）、
  CONTEXT「商店预览」「商店分类与安装量」「卸载与解绑」
- 关联规格：`docs/superpowers/specs/2026-09-22-zerocode-app-store-design.md`（M3-a，已交付；
  其 §6.4~6.6、§7、§9、§10 即本票 M3-b 部分的实施依据，本规格不重复其内容）

## 1. 问题陈述

商店现状是一条纯管道：上架 → 创始人审批 → 全自动安装。三个盲区：

1. **盲装**：装之前无法判断应用值不值得装。一次安装是重型流水线
   （`app/store/install.py`：铺源码 + 建 dev/prod 分库 + 基线重放 + 构建镜像 + 起 prod 栈体检），
   试错成本极高，而商店条目没有任何截图、详情或试用入口。
2. **逛不动**：`StoreView.vue` 一页三 Tab，卡片只有标题+技术栈+发布者+版本+简介；
   无搜索、无分类、无排序、无详情页、无分页。`app_release` 表无截图/分类/安装量字段。
3. **装错无出口**：`app_instance_link` 无 DELETE；申请无撤回入口（status 枚举含 cancelled
   但无路由）；已装实例「打开」硬编码 `http://localhost:{port}`，远程部署下根本打不开；
   M3-b 升级/fork 未实现（`upgradable` 恒 False）。

## 2. 方案概述

四块能力，一次交付：

- **商店预览**（ADR-0057）：截图展示（选填、占位封面）+ 在线试用（复用作者 dev 预览栈，
  `status_dev` 闸门，停了置灰不代启，地址复用既有 `preview_url` 投影）。
- **发现体验**：关键词搜索 + 平台固定分类 + 排序（最新/安装量）+ 安装量展示 + 详情抽屉 + 前端分页。
- **轻量生命周期收口**：申请撤回、已装实例「打开」改 `nics.app_url` 投影、卸载+解绑双出口。
- **M3-b 一致性与升级**：按 M3-a 规格 §6.4~6.6 原样实施（影子克隆预演、手动升级、fork 问人、
  启动对账扩展），不重新设计。

### 2.1 接缝契约（API 形状）

沿用 `app/api/store.py` 薄层 + `app/store/*` 服务层纪律，DeployError.status 映射 HTTP：

| 接缝 | 形状 | 说明 |
| --- | --- | --- |
| `GET /api/store/releases` | 增 `q`/`category`/`sort` 查询参；响应每项增 `category`、`screenshots`（URL 列表）、`install_count`、`trial_available`、`trial_url` | `sort=latest\|installs`；`trial_*` 按来源应用 `status_dev` 运行时投影；`updated_count` 字段移除（现前端未消费） |
| `POST /api/apps/{id}/release` | `ReleaseIn` 增可选 `category`、`screenshots`（multipart 或先传后引，见 §4.3） | 五道闸门不变；新字段不进 anchor/fingerprint |
| `GET /api/store/releases/{id}/screenshots/{n}` | 登录守卫内 FileResponse | 截图不挂公共静态，走鉴权路由（同 attachments 纪律） |
| `POST /api/store/releases/{id}/screenshots` / `DELETE …/{n}` | 仅该 release 的 created_by | 存量条目补传/替换/删图 |
| `POST /api/store/applications/{aid}/cancel` | 仅申请人、仅 pending → cancelled | 沿用发布申请「申请人可撤回」惯例（CONTEXT「应用共享」） |
| `POST /api/store/link/{app_id}/unlink` | 仅 owner；删 link 行、清 origin 标记 | 应用转普通自有，容器/库/源码全留 |
| `DELETE /api/store/link/{app_id}` | 仅 owner；卸载全链（§4.4） | 高危；`_reject_if_task_running` 闸门沿用 |
| `GET /api/apps/{id}/store-link` | `upgradable` 实装 + `latest_release`/`detach_reason` | M3-a 规格 §8 已定形状 |
| `POST /api/apps/{id}/store/upgrade`、`POST /api/apps/{id}/store/fork` | M3-a 规格 §6.4/§6.5/§8 | 本票不另定形状 |

前端接缝：`StoreView.vue`（列表/详情抽屉/撤回）+ `AppsView.vue` 卡片 ☰ 菜单（卸载/解绑/升级角标，
遵循既有卡片操作分层约定）+ `frontend/src/api` 增对应方法。

## 3. 用户故事

### 发现体验

1. 浏览者在商店 Tab 输入关键词，按标题+简介过滤条目（后端 `q` 参数，ILIKE）。
2. 浏览者按固定分类筛选（含「全部」）；分类枚举初值：效率工具 / 数据管理 / 内容展示 /
   演示样例 / 其他；上架时选填，缺省「其他」。
3. 浏览者按「最新上架 / 安装量」排序，默认最新。
4. 浏览者在卡片与详情看到安装量＝该条目**被批准安装**的次数（approved 申请计数），
   不是申请数；卸载不回扣。
5. 无截图条目的卡片显示占位封面（技术栈色块 + 图标），不出现破图。
6. 浏览者点卡片打开**详情抽屉**：截图轮播、完整简介、分类/技术栈/发布者/版本/安装量/上架时间、
   试用与申请入口、本人已装实例列表。
7. 列表前端分页（每页 20 条）；筛选/排序变化回到第一页。

### 商店预览

8. 作者上架时可选填上传 ≤5 张截图（PNG/JPEG 魔数校验、单张 ≤2MB，沿用移动端图标签别纪律）；
   超数/超大小/错格式以明确错误拒收（400/413）。
9. 作者可在详情抽屉的管理入口（仅 created_by 可见）为已上架条目补传/替换/删除截图。
10. 来源应用 dev 栈 running 时，卡片与详情的「在线试用」可点，`window.open` 新标签打开
    来源应用的 `preview_url`（与「打开预览」同源投影）。
11. 来源应用 dev 栈非 running 时，「在线试用」置灰并提示「作者的测试环境未启动」；
    **任何角色都没有代为启动的入口**（ADR-0026 哲学，ADR-0057）。
12. 试用入口旁有告知文案：试用环境是作者的测试环境，试用操作会写入作者的测试数据库。
13. 截图按 release 存放在 store 目录（运行时挂载区），**不进应用工作区、不进包、
    不进 source_hash/baseline_hash**——安装者传图不触发分叉误报（同 backend/uploads 教训）。

### 生命周期收口

14. 申请人在「我的申请」对 pending 申请点「撤回」→ cancelled，创始人收件箱不再出现；
    已批准/已驳回/已撤回的申请不可再撤回（409）；非申请人撤回 403。
15. 已装实例的「打开」使用 `nics.app_url` 投影（SSL 开＝网关子域名，关＝host:prod_port），
    与「打开生产」同口径；`status_prod` 非 running 时置灰，不代为启动。
16. 实例 owner 在 ☰ 菜单点「解绑」→ 确认后删 link、清 origin：商店角标消失、不再接收升级，
    应用转为普通自有应用；容器/库/源码/会话全保留。
17. 实例 owner 在 ☰ 菜单点「卸载」→ 高危确认（danger 确认弹层，文案列明将删除的内容）→
    停栈 → drop dev/prod 库与角色 → 删平台记录（CASCADE 清 link/会话）→ 删源码目录 → 释放端口；
    各步失败**诚实报错**，不吞错假绿。
18. 非 owner（含被共享者）调解绑/卸载 403，前端不展示这两个菜单项；
    卸载/解绑与发布/安装/升级任务互斥（`_reject_if_task_running`，409）。

### M3-b 升级与 fork（按 M3-a 规格实施，验收以该规格为准）

19. 实例 owner 在卡片看到「来自商店 vN」角标；有新版时出现「升级」入口
    （M3-a 规格 §9：复用 PublishModal 进度/失败弹层形态，断连窗口写进提示文案）。
20. 手动升级走：前缀校验 → 影子克隆预演 → 真实迁移 → 程序切换 → 体检；
    失败整体退回升级前状态，prev 库留作人工复原入口（§6.4）。升级只作用于 prod 库。
21. 检测到代码/SQL 分叉时「问人」：保留定制 → 永久 detached、升级置灰、「已定制」角标；
    丢弃定制回官方 → 包源码覆盖、库保留、重置 baseline、回 managed（§6.5）。
22. 启动对账按 §6.6 扩展；上架线性前缀闸门（§6.1-3）保持不变并回归。

## 4. 已定决策

### 拷问共识（不再翻案）

- D1 预览＝截图展示 + 在线试用两层；不做沙箱、不做按人临时栈（ADR-0057 备选方案节）。
- D2 在线试用复用作者 dev 预览栈；prod 永不承担试用；试用数据写作者 dev 库（演示数据范畴）。
- D3 闸门＝`status_dev === running` 运行时检查；停了置灰、不代为启动；不上架强制闸门。
- D4 截图选填（≤5 张），无图占位封面；存量条目零迁移、不强制补传。
- D5 发现体验全套：搜索 + 平台固定分类（选填缺省「其他」，不做自由标签）+ 排序 + 安装量 + 详情页。
- D6 范围全量：含 M3-b 升级/fork，按 M3-a 规格既有口径实施，不重新设计。
- D7 申请可撤回，沿用发布申请惯例。
- D8 已装实例「打开」复用 `nics.app_url` 单点投影，弃 localhost 硬编码。
- D9 卸载+解绑双出口；卸载属破坏性操作，走高危确认；解绑保留应用。

### 规格层决策（本规格敲定，实施不再问）

- §4.1 详情形态＝页内抽屉（a-drawer），不新增路由；商店保持一页三 Tab 骨架。
- §4.2 分页在前端做（条目规模小，后端仍回全量）；后端游标分页明确不做。
- §4.3 截图上传走 multipart（同 `mobile_upload_icon` 先例：魔数 + 大小闸），
  上架与补传共用同一服务函数；落盘 `store/releases/{release_id}/shot-{n}.{ext}`，
  数据库只存相对路径列表（JSONB）。
- §4.4 卸载全链＝`engine.down_app_stacks(slug)` 先行，再复用 `install._teardown` 的
  drop_env → delete_app → rmtree 链，但**不沿用其吞错语义**：卸载是用户主动操作，
  任一步失败必须回报（回滚路径才允许 best-effort）。link 行随 app_project CASCADE 消失，
  不另删。
- §4.5 安装量＝`release_application` 中 status='approved' 的计数（稳定、卸载不回扣）；
  移除 `updated_count`（现前端未消费，无兼容负担）。
- §4.6 试用可用性是列表加载时快照；加载后作者停 dev 导致的陈旧可点态接受
  （点开即不可达，与既有「打开预览」同语义），不做点击时二次校验。
- §4.7 迁移为线性追加：新 changelog 文件给 `app_release` 加 `category VARCHAR` 与
  `screenshots JSONB`（均可空/缺省），无破坏性变更，不触既有 baseline。
- §4.8 分类枚举初值写死在后端常量 + 前端同源镜像（同 stack 标签纪律），不进 app_config
  （避免 conftest 种子重放连带）；改分类属代码变更。

## 5. 测试决策

**最高接缝优先**：绝大多数行为钉在 REST 接缝（`tests/test_store_api.py` 既有靶心），
服务层只补 REST 覆盖不到的判定；前端钉 `StoreView.test.js` / `AppsView.test.js`（VTU 既有基建）。

| 接缝 | 新增断言 |
| --- | --- |
| `GET /releases`（test_store_api.py） | q/category/sort 过滤排序正确；install_count 只数 approved（pending/rejected/cancelled 不计）；trial_available/trial_url 随来源应用 status_dev 变化；updated_count 不再出现 |
| 上架与截图路由 | 选填缺省「其他」；>5 张/错格式/超大小拒收；非 created_by 补删 403；截图路由未登录 401；截图落盘在 store 目录、**不进包与 baseline_hash**（对 test_store_packages.py 既有排除断言做反向钉） |
| cancel 路由（test_store_applications.py） | pending→cancelled；非申请人 403；非 pending 409；收件箱不再出现 |
| unlink/unlink 路由（test_store_link.py） | unlink 后 link 消失、origin 清空、应用仍在；uninstall 全链（桩 docker/DB，沿用 test_store_install_flow.py 桩法）：停栈先于删库删记录；失败诚实报错；非 owner 403；任务运行中 409 |
| 打开地址 | installed_apps 项含 prod_url 且走 nics.app_url（SSL 开/关两态，复用 nics 既有测试靶心） |
| M3-b | 按 M3-a 规格 §10 测试清单：test_store_fork.py（hash 变更→detached；reset_to_official 回 managed 且库保留）、影子预演/回滚、前缀校验拒升 |
| StoreView.test.js | 搜索/筛选/排序/分页交互；详情抽屉内容；试用按钮 running 可点 / 非 running 置灰且无代启入口；撤回按钮仅 pending 出现；打开用 prod_url 且 stopped 置灰 |
| AppsView.test.js | ☰ 菜单卸载/解绑按 owner 裁剪；卸载高危确认文案；升级角标与入口（M3-b） |

**完成定义**：上表断言全绿 + 全量 pytest 串行通过 + 前端 vitest（--maxWorkers=4）通过 +
浏览器实测 golden path（上架带图 → 搜索筛选 → 详情试用 → 申请撤回 → 批准安装 → 打开网关地址 →
解绑/卸载 → M3-b 升级与 fork 问人）。守卫类断言按变异测试纪律钉接线（纯函数有测试≠被调用）。

## 6. 明确不做

- 平台沙箱演示实例、按试用者起临时栈、只读试用拦截、作者试用开关（ADR-0057 已否）。
- prod 承担试用、试用者代为启动作者 dev 栈、闲置自动回收。
- 作者自由标签与标签治理；分类进 app_config 动态配置。
- 后端游标分页；评分/评论/下载榜/推荐位。
- 截图必填、存量条目强制补传、无图条目下架。
- 自动升级推送（D4 手动）、合并升级（D6 线性）、跨平台中心商店与包下载协议（M3-a 非目标延续）。
- static 栈上架（既有闸门 0 不动）；审批权扩散（仍收口创始人）。
- 试用点击时二次校验、试用预约/排队、试用数据定期重置（dev 库重置沿用既有「重建 dev 库」，不新做）。

## 7. 补充说明

- **试用地址可达性**：`preview_url` 是直连地址（裸 IP/localhost），可达范围与既有「打开预览」
  完全一致——远程部署下 dev 端口的可达性是既有约束，本票不改变也不承诺解决。
  直连形态天然绕开 Vite allowedHosts 对网关域名的 403（2026-10-28 实测坑，ADR-0057 后果节）。
- **截图与分叉判定隔离**：截图住 store 目录（release 维度），不住应用工作区——
  这是 backend/uploads 教训的直接复用：任何「安装者/浏览者产生的内容」不得进 source_hash，
  否则下一次发布被误判分叉。
- **M3-b 独立回归**：它动真库（影子克隆/预演/回滚），按 M3-a 规格要求独立评审与独立回归；
  全量 pytest 必须串行（共享 zerocode_test 库并发 TRUNCATE 会死锁），且会 down 掉宿主 dev 预览栈，
  跑前确认无 zc-*-dev/prod 容器在用。
- **conftest 种子**：本票不新增平台级 app_config 默认键（§4.8），不触发种子重放连带。
- **卸载确认形态**：沿用平台既有应用删除的 danger 确认弹层；文案必须列明「停栈、删两个库、
  删源码目录、删会话记录」四项后果，与 SQL 控制台确认词纪律同源但不要求输入确认词
  （应用删除既有形态即如此，保持一致）。