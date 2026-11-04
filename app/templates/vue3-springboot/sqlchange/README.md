# sqlchange —— 数据变更目录（数据轨：仅种子/回填/刷值，不可从结构推导）

- 只放**必须在生产显式执行、且无法从结构推导**的数据动作：初始种子、必要回填、字典刷值
- 结构变更不放这里——去 `../schema/schema.sql` 改目标态（声明式真相）
- 命名 `D<YYYYMMDD>_<两位序号>_<说明>.sql`；**只追加、不可变、幂等、按序执行**
- 种子一律 `INSERT ... ON CONFLICT ... DO NOTHING`；回填脚本只追加不回改

## 三段：公共段 / `dev/` / `prod/`（ADR-0040）

一次迁移执行跑的是**公共段（本目录顶层）+ 本环境段**，另一段的文件对这个库永不可见：

| 位置 | 进哪个库 | 放什么 |
| --- | --- | --- |
| `sqlchange/*.sql`（顶层） | dev 与 prod 都跑 | 存量历史文件；以及**两库内容完全一致**的新脚本（如破坏性变更的回填刷值） |
| `sqlchange/dev/*.sql` | 只进 `<slug>_dev` | 演示数据：测试用户、示例订单、为了让预览有看头而灌的行 |
| `sqlchange/prod/*.sql` | 只进 `<slug>_prod` | 上线必需初始行，以及与 dev 段内容不同的那部分（真实菜单/字典） |

- 分派看**数据用途**不看表名：同一张 `sys_menu` 里，真实菜单进 prod 段，为截图塞的假菜单只进 dev 段。
- 参考数据（菜单、字典、角色授权、系统参数）上线也要生效：要么 `dev/` 与 `prod/` 各写一份，
  要么内容一字不差时只写顶层；**别让它只躺在 dev 段**，那等于线上缺一份配置。
- 记账与打包用**相对本目录的路径**（`dev/D….sql`），所以 dev 段与 prod 段可以有同名文件、互不干扰。
- dev 预览与 prod 发布都只执行「本库上次记账之后新增的」脚本（各库一张 `sqlchange_history`）。
- 存量应用没有这两个子目录是合法的（历史文件全在顶层，不回填）；首次拆环境时自己建目录即可。
- 遗留的 `V*.sql`（结构增量文件）仍按原样增量执行（向后兼容），新结构请写进 schema.sql
- 涉及数据回填的破坏性变更，也在本目录补一条幂等 `D*.sql` 回填脚本（两步走的第二步）

## 本栈特有：菜单也要走数据轨

路由是前端**静态注册**的（`frontend/src/router/index.ts`），`sys_menu` 只决定侧边栏可见性与路由守卫的放行集合
（同文件的 `beforeEach`：当前路径命中不了菜单集合就跳 `/403`）。所以新增业务页必须补一个 D 文件，否则页面点开就是 403：

```sql
INSERT INTO sys_menu (id, parent_id, menu_name, menu_type, path, component, permission, icon, sort, visible)
VALUES (1200, 0, '工单管理', 'C', 'ticket', 'business/ticket/index', 'biz:ticket:list', 'ProfileOutlined', 20, 1)
ON CONFLICT (id) DO NOTHING;
INSERT INTO sys_role_menu (role_id, menu_id) SELECT 1, 1200 ON CONFLICT DO NOTHING;
SELECT setval('sys_menu_id_seq', (SELECT MAX(id) FROM sys_menu));
```

- 显式写 id，且**取大于 1072**（基座已占用 `1`、`2`、`100–107`，并预留了 `1011–1072`：1011 起按「父菜单 id ×10 + 序号」编按钮）；一个文件一个逻辑变更。
  不要图省事挑中间的空隙（如 200、1015）：踩到已占用的 id 时，`ON CONFLICT (id) DO NOTHING` 会**静默跳过**，
  菜单不出现也不报错，是最难查的一类「种子跑了但没生效」。补完记得前移 `sys_menu_id_seq`（基座的 id 是写死的，不前移则后台新增菜单必主键冲突）。
- 还要给角色授权（上面的 `sys_role_menu`）：基座只给 admin 角色全量授权过一次，新菜单不补这一行就是「菜单存在但谁也没权」。
- `path` 与路由表同一口径：顶层菜单写片段即可（拼接后是 `/ticket`），子级相对片段会拼上父级完整路径（`system` + `user` → `/system/user`）；
  拼接逻辑在 `frontend/src/utils/menuPath.ts`，侧边栏与守卫共用它，两边不会出现两套口径。
- `menu_type`：`M`=目录、`C`=菜单、`F`=按钮。后端 `/auth/routers` 只下发 M/C，`F` 不进侧边栏，只进 `permissions` 集合，
  供 `useUserStore().hasPermission('biz:ticket:add')` 做按钮级显隐（admin 恒过）。
