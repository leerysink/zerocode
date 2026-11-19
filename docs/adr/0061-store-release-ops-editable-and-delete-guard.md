# 0061 商店版本条目的运营可编辑性与删除守卫

- 状态：Accepted
- 日期：2026-11-20
- 关联：ADR-0057（商店试用）、ADR-0014（init.sql 由 changelog 合成）、
  CONTEXT「运营编辑」「硬删与下架」「分类字典与分类 key」、
  `docs/adr/0055-console-theme-unified-light-dark.md`（管理页换肤同源）

## 背景

商店管理页 `/store-admin` 是「能看不能改」的半成品：写操作只有下架、重新上架、批准、驳回，
后端对 `/api/store/releases/{id}` 没有任何 PUT/PATCH（`app/api/store.py`）。创始人遇到标题错别字、
摘要夸大、分类放错，唯一出路是驳回让作者重发一次——那会新增一条 `app_release` 行、重跑一遍审批，
在版本历史里留下垃圾行。要把管理页升级成真正的运营后台，必须先划清「运营能改什么」的边界。

## 决策

1. **编辑只动描述性元数据**：新增 `PATCH /api/store/releases/{id}`，字段面固定为
   `title` / `summary` / `category`；截图沿用已有的增删端点。`slug` / `version` / `stack` /
   `status` / `app_id` 一律不在面内——它们分别是安装标识、包目录名与技术栈，改动会牵连
   已安装实例与商店数据一致性双轨。权限沿用创始人门禁。
2. **留痕走行上两列，不建编辑历史表**：changelog 给 `app_release` 加 `updated_at` /
   `updated_by`，管理卡与作者侧显示「已由创始人编辑」。独立 diff 表被否——当前诉求只是
   「作者能看出条目被平台改过」，不是逐字段审计。
3. **硬删有引用守卫**：`app_release(id)` 被 `app_release.prev_release_id`、
   `release_application.release_id`、`app_instance_link.release_id` 三处 NOT NULL 引用，
   且都没有 `ON DELETE CASCADE`。因此硬删只对**无任何引用**的版本行开放，有引用一律拒绝并提示
   「先下架」。不加 CASCADE 迁移：一次误点会连带抹掉别人的申请历史与实例溯源，
   违反破坏性变更需人工确认的既有纪律。
4. **分类改字典表，关系仍存 key**：新表 `store_category(key, label, sort, enabled)`，
   `app_release.category` 继续存 key（`VARCHAR(20)` 不改列、不回填）。「其他」是代码层兜底值，
   在字典里锁死不可删；有条目引用的分类只允许 `enabled=false` 停用，不允许删除。

## 备选方案

- **`category` 改 `category_id` 外键**：数据库层约束最严；但要回填存量、改商店筛选与打包链路，
  而收益（引用完整性）在写入处做一次检查即可覆盖。被否。
- **编辑后回到待审批**：把「描述性修改」也过一遍审批；但创始人本身就是审批收口角色
  （见 CONTEXT「创始人」），等于自己审自己，只增加状态机分支。被否。
- **不做硬删，只有软删**：最保守，代价是磁盘与列表里永久留着无引用的垃圾行，
  运营清理没有出口。被否。

## 后果

- 迁移面收敛在**一个 changelog 文件**（加两列 + 建 `store_category` + 种子现有枚举值）。
  `init.sql` 由 `scripts/build_output.py` 按 ADR-0014 从 changelog 合成，全量轨与增量轨
  没有第二个真源；changelog 内照旧禁止 `$$` 块与含分号的字符串字面量。
- 商店页与详情页的文案会因运营编辑**立即变化**，不经过作者——这是「平台代作者说话」的
  刻意授权，靠 `updated_by` 留痕而非流程拦截来约束。
- 「先下架才能删」会让运营觉得删除按钮不灵敏；卡上需要在拒绝时把引用计数（安装数/申请数）
  显示出来，否则反馈读起来像 bug。
