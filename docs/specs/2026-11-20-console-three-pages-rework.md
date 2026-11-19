# 2026-11-20 控制台三页重构：运营台可编辑 + 薄组件统一

- 状态：Accepted（grill 对齐完成，待拆分）
- 日期：2026-11-20
- 关联：ADR-0061（运营可编辑性与删除守卫）、ADR-0062（薄组件边界与导航形态）、
  ADR-0048（应用卡语言）、ADR-0055（主题统一）、ADR-0014（init.sql 由 changelog 合成）、
  CONTEXT「运营台」「运营编辑」「硬删与下架」「分类字典与分类 key」「薄组件」「创始人」

## 1. 问题陈述

四个控制台页面里只有应用页 `AppsView.vue` 经过一轮卡片语言设计（ADR-0048），其余三页各自长成
另一副样子，并排放像四个产品。更要紧的是能力缺口：

- **运营台 `/store-admin` 能看不能改**。写操作只有下架、重新上架、批准、驳回；后端对
  `/releases/{id}` 没有任何 PUT/PATCH（`app/api/store.py` 端点清单已确认）。标题错别字、摘要夸大、
  分类放错，唯一出路是驳回让作者重发一次——新增一条 `app_release` 行、重跑审批，历史里留垃圾行。
  它同时是四个页面里唯一没有返回按钮的页面，点封面只在 `preview_url` 非空时新标签打开，
  既无详情层也无编辑层。
- **商店页 `/store` 卡片动作是 `a-space` 里 1~N 枚文字按钮**（试用 / 申请 / 每个已装实例各一枚），
  实例多时按钮溢出；且它内部另有一份创始人「待审批」页签（`StoreView.vue:215`），与运营台的审批
  队列抽屉功能重叠，两处队列存在完成时间差。
- **设置页 `/settings` 是 `a-tabs` 里塞两大块长表单**，模型库用 `a-table` CRUD；主题开关藏在
  `AccountBar.vue` 没进设置页；「运行参数」页签里混着上下文压缩、联网工具、记忆召回等 6 个不相干分区。

## 2. 方案概述

后端把运营台需要的三个能力补成真接口：**运营编辑**（描述性元数据 PATCH + 行上留痕）、
**硬删守卫**（无引用才真删）、**分类字典**（新表，关系仍存 key）。

前端抽三个薄组件（`ZcPageHeader` / `ZcEntityCard` / `ZcEmptyState`，只收 props 与插槽），
三页与应用页全部换用它们；网格统一为**三列等高 + 摘要两行截断**，应用页跟改。
设置页改成页内左侧二级导航 + 右侧分区，模型库保留表格。审批入口收口到运营台。

垂直切片顺序：**迁移与端点 → 薄组件 → 运营台 → 商店页 → 设置页**。前一竖切交付即可用 HTTP
验证，后四片各自独立可测。

## 3. 用户故事

### A. 运营编辑（后端）

1. 创始人对版本行 `PATCH /api/store/releases/{id}` 改 `title` / `summary` / `category` → 200，
   返回更新后的行，`updated_at` / `updated_by` 同时落库。
2. 非创始人调用 → 403，沿用既有文案「商店管理仅创始人可见」。
3. 请求体出现白名单外字段（`slug` / `version` / `stack` / `status` / `app_id`）→ 400 明确拒收，
   **不静默忽略**（否则运营以为改成了）。
4. `category` 不在启用字典内 → 400，口径同上架时「非法分类最先拒 400」。
5. `title` 为空或超 100 字（`VARCHAR(100)`）→ 400。
6. 编辑一条 published 行后，`GET /api/store/releases` 与详情立刻读到新文案，无需重新审批。
7. 编辑不新增 `app_release` 行，不影响 `UNIQUE (slug, version)` 与可升级判定。

### B. 硬删守卫（后端）

8. `DELETE /api/store/releases/{id}`，该行无任何引用 → 真删，行从 `admin/releases` 列表消失。
9. 被 `release_application.release_id` 指向 → 409，响应带引用计数，行保留。
10. 被 `app_instance_link.release_id` 指向 → 409 同上。
11. 被其它行 `app_release.prev_release_id` 指向 → 409 同上。
12. 非创始人 → 403。
13. `withdrawn` 行同样受守卫约束——下架状态不是免死金牌。
14. 守卫检查与删除在同一事务内完成，不出现「检查时无引用、删除时被并发安装」留下 FK 报错裸奔。

### C. 分类字典（后端）

15. `GET /api/store/categories` → 全部字典行（含停用），按 `sort` 升序；在登录守卫内。
16. 上架与运营编辑的可选分类只取 `enabled=true`；「其他」恒在列内。
17. `POST` 新建（key/label/sort）→ 201；key 重复 → 409；key 长度 > 20 → 400。
18. `PATCH` 改 label / sort / enabled → 200。
19. 停用某分类后：存量条目详情页与卡片仍显示它的 label，但新建与编辑的下拉里不再出现。
20. `DELETE` 分类：有任意条目引用 → 409 并回引用计数；无引用 → 真删。
21. 「其他」在任何路径下都不可停用、不可删除、不可改 key。
22. 字典的三个写端点仅创始人可调。

### D. 迁移与测试库（后端）

23. 新增 `changelog/V20261120_01_store_ops_editable.sql`：给 `app_release` 加
    `updated_at TIMESTAMPTZ` / `updated_by BIGINT REFERENCES actor(id)`；建 `store_category`
    （`key VARCHAR(20) PRIMARY KEY, label VARCHAR(40), sort INT, enabled BOOL`）；按现存 5 个值
    种子（key 与 label 同值）。全部语句重放安全（`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`），
    文件内禁止 `$$` 块与含分号的字符串字面量。
24. `tests/test_deploy_migrations.py` 与 `tests/test_build_output.py` 绿：`init.sql` 合成结果
    含新表与新列（全量轨与增量轨无第二个真源）。
25. `tests/conftest.py`：把 `store_category` 加进 TRUNCATE 名单，并在种子段重放 5 行——照
    `app_config` 的先例办，否则分类用例跨轮次泄漏状态。

### E. 薄组件（前端）

26. `ZcPageHeader`：props `title` / `subtitle` / `stats[]` / `back`（默认 true）；渲染
    `BackButton`（固定回 `/`）+ 页头 + 统计格。
27. `ZcEntityCard`：插槽 `cover` / `title` / `meta` / `summary` / `actions` / `menu` / `footer`；
    **等高与摘要两行截断内建**，不做成 prop；左缘状态轨走 `--zc-rail` 内联变量；菜单槽固定放
    `menu-outlined` 三横图标（不用 ⋯）。
28. `ZcEmptyState`：`loading` 态（`a-spin.zc-spin`，`margin:60px auto`）与空态（`a-empty`）合一。
29. 三个组件各带独立 vitest（happy-dom + 桩 `../api`，`data-test` 定位）。
30. `AppsView.vue` 换用三组件后，`AppsView.test.js`（1420 行）**全绿且不改断言**——选择器不动是
    本片的验收条件之一。
31. 应用页网格从 `repeat(auto-fill, minmax(480px,1fr))` 不等高改为三列等高，仪表带
    `.zc-band`（`72px minmax(0,1fr) auto`）在新卡宽下不破版（CPU 环 + 双 sparkline + 三读数仍齐）。
32. 新组件的间距、字阶、圆角一律取 `--zc-space-*` / `--zc-radius-*` / `--zc-shadow-1`，
    深色块不覆写几何 token。

### F. 运营台（前端）

33. 页头有返回按钮，行为与其余三页一致（回 `/`）。
34. 每张版本卡有 ☰ 菜单：编辑 / 查看详情 / 下架或重新上架 / 删除。
35. 「编辑」开表单弹框（`title` / `summary` / `category` 三字段 + 截图增删）；保存成功 →
    提示 + 列表刷新 + 卡上出现「已由 <发布者昵称> 编辑 · <时间>」副行。
36. 「查看详情」开抽屉，含：预览地址、截图、身份字段只读区（`slug` / `version` / `stack` /
    状态）、发布者、安装数与申请数、审批信息、编辑留痕。
37. 「删除」是二次确认（`ok-type="danger"`，文案写明不可恢复）；服务端回 409 时，提示必须显示
    引用计数（「已有 N 次安装、M 条申请，请先下架」），否则读起来像 bug。
38. 分类总览弹窗升级为字典管理：列表 + 新建 + 改名/改排序 + 停用；「其他」行操作禁用并说明原因。
39. 三 tab 计数、审批队列抽屉、审批徽标保留；徽标可点直达抽屉。
40. `NavRail` 的「管理」入口显示待审批红点，仅创始人可见，数据源 `storeApplications('review')`。

### G. 商店页（前端）

41. 删除「待审批」页签，只留「商店 / 我的申请」；审批动作全部发生在运营台。
42. 卡片动作改两枚胶囊：**在线试用**（`trial_available` 口径、disabled 与 tooltip 文案不变）+
    **申请使用**（已装则显示「打开」）；多个已装实例收进 ☰ 菜单。
43. 页头改 `ZcPageHeader` + 统计格（在售条目数、启用分类数、我的安装数）。
44. 搜索、排序、分类胶囊统一到 token，筛选与分页**行为不变**。
45. 整卡可点进详情抽屉的既有习惯保留（封面与标题都能开）。

### H. 设置页（前端）

46. 改为页内左侧二级导航 + 右侧内容区，8 个分区：模型库 / 模型接入 / Agent 行为 /
    上下文与记忆 / 联网与工具 / 环境与预览 / 外观 / 账号。切换不碰路由。
47. 模型库保留 `a-table`（密集字段表格更可读），行操作改 ☰ 菜单 + 编辑弹框；字段与校验不变。
48. 外观分区提供主题开关与说明；账号分区显示昵称、创始人标识与登出。
49. 各分区内部长表单改成分组卡（标题 + 说明 + 控件），说明文字沿用 `.zc-modal-tip` 的字号与色阶纪律。
50. 分区状态在页面内切换后保持选中（刷新回落第一个分区即可，不做持久化）。

## 4. 已定决策

| # | 决策 | 出处 |
|---|---|---|
| 1 | 编辑字段面固定为 `title/summary/category`，身份字段不可改 | ADR-0061 §1 |
| 2 | 留痕走行上两列，不建编辑历史表、不做逐字段 diff | ADR-0061 §2 |
| 3 | 硬删只对无引用行开放，拒绝时回计数；不加 `ON DELETE CASCADE` | ADR-0061 §3 |
| 4 | 分类落字典表，`app_release.category` 继续存 key，不建外键不回填 | ADR-0061 §4 |
| 5 | 只抽三个薄组件，不封 antd 基础层、不抽业务组件 | ADR-0062 §1/§3 |
| 6 | 网格统一三列等高，应用页跟改（部分改写 ADR-0048 的卡面尺寸） | ADR-0062 §6 |
| 7 | 审批入口收口运营台，待审批数上 NavRail 徽标 | ADR-0062 §7 |
| 8 | 设置页用页内二级导航，不开 `/settings/*` 子路由 | ADR-0062 §4 |
| 9 | 返回按钮沿用 `BackButton` 固定回 `/`，四页统一 | ADR-0062 §5 |
| 10 | 模型库保留表格，不因「统一」转卡片 | 本轮对齐 |
| 11 | 直接在 master 工作区做，分 5 笔提交，完工一次性起服务走查 | 本轮对齐 |

## 5. 测试决策

**最高接缝优先**：后端三个能力全部走 HTTP 层（`tests/test_store_api.py` 既有套路），
不在 service 函数层另开一套靶子。

- **后端 HTTP 接缝**：`client`（已登录非创始人）、`two_auth_clients`（`alice` 首账号 = 创始人、
  `bob` 普通账号，判定见 `app/api/auth.py:48`）。故事 1–22 全部在这一层测。
- **迁移接缝**：`tests/test_store_migration.py`（表/列存在性）+ `tests/test_deploy_migrations.py`
  + `tests/test_build_output.py`（`init.sql` 合成）。故事 23–25。
- **前端视图接缝**：`StoreAdminView.test.js` / `StoreView.test.js` / `SettingsView.test.js` /
  `SkillsView.test.js` 各自的 `mountView` + `vi.mock('../api')` 桩（**按 api 函数名桩**，
  新增客户端函数需同步补桩）；故事 33–50 落在视图层。
- **组件接缝**：新建 `ZcPageHeader.test.js` / `ZcEntityCard.test.js` / `ZcEmptyState.test.js`。
- **回归网**：`AppsView.test.js` 1420 行不改断言必须全绿——这是「换壳不改行为」的判据。
- **完成定义**：后端 `pytest`（串行，全量约 27 分钟，且会 down 掉本机 dev 预览栈）+ 前端
  `vitest --maxWorkers=4` 全绿 + `npm run build` 通过 + 完工一次性起 dev 栈逐页走查
  （编辑保存、硬删被拒、分类停用、三页视觉与返回）。

## 6. 明确不做

- 不放开 `slug` / `version` / `stack` 的编辑，不动 `UNIQUE (slug, version)`。
- 不做编辑历史表与逐字段 diff 审计；不做编辑后回审批。
- 不加 `ON DELETE CASCADE`；不做回收站延迟清理。
- 不开 `/settings/*` 与 `/store-admin/:id` 子路由（`/skills` 在 5ec4baf 为避开父路径高亮串台
  才升为一级入口，不重踩）。
- 不包装 `a-card` / `a-modal` / `a-drawer`，不改 DbAdminView、聊天卡片与其它页面。
- 本轮不动 `/skills` 页与 `NavRail` 的入口清单（只加一个徽标）。
- 不做分类多语言、收藏、评分、评论、举报。
- 不做编辑后主动通知作者或强制刷新已安装用户视图（留痕仅展示）。
- 不 push、不建 PR、不合分支——等明确指令。

## 7. 补充说明

- **`category` key 就是中文 label**：现存 5 个值（效率工具 / 数据管理 / 内容展示 / 演示样例 / 其他）
  key 与 label 同值，前端 `utils/storeCategories.js` 是后端 `app/store/release.py:22` 的同源镜像。
  字典表落地后这个镜像应改成从 `GET /store/categories` 取，**否则两处枚举会漂移**。
- **新建分类的 key 上限 20 字符**由 `app_release.category VARCHAR(20)` 硬约束决定，不是字典表自己
  的选择。要放开得另开扩列迁移。
- **主题开关两处并存**：`AccountBar.vue:33-38` 的快捷开关保留（`AccountBar.test.js` 有 4 处
  `theme-toggle` 断言），设置页外观区是第二个完整入口。若要收成一处，需同步改那个测试。
- `ChatView.test.js` 把 `NavRail.vue` 整体桩掉，加徽标不会打爆它；但 `NavRail` 自身若新增取数，
  要注意它挂在 ChatView 上、每次进首页都会发一次 `storeApplications('review')`。
- 运营台与商店页都改成等高卡后，`StoreView.vue:316` 那句「同纪律」的注释约定可以删掉——
  纪律已经进组件了。
- 提交前重拍 `git status` 并按文件暂存：master 工作区常有并行会话的 WIP。
- 术语口径见 CONTEXT「运营台」「运营编辑」「硬删与下架」「分类字典与分类 key」「薄组件」；
  「管理页」在本域一律指 `/store-admin`，不含应用数据库的查询控制台。
