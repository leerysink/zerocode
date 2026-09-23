# sqlchange —— 数据变更目录（数据轨：仅种子/回填/刷值，不可从结构推导）

- 只放**必须在生产显式执行、且无法从结构推导**的数据动作：初始种子、必要回填、字典刷值
- 结构变更不放这里——去 `../schema/schema.sql` 改目标态（声明式真相）
- 命名 `D<YYYYMMDD>_<两位序号>_<说明>.sql`；**只追加、不可变、幂等、按序执行**
- 种子一律 `INSERT ... ON CONFLICT ... DO NOTHING`；回填脚本只追加不回改
- dev 预览与 prod 发布都会执行「上次记账之后新增的」脚本（sqlchange_history 记账）
- 存量遗留的 `V*.sql`（结构增量文件）仍按原样增量执行（向后兼容），新结构请写进 schema.sql
- 涉及数据回填的破坏性变更，也在本目录补一条幂等 `D*.sql` 回填脚本（两步走的第二步）

## 本栈特有：菜单也要走数据轨

路由是前端**静态注册**的（`frontend/src/router/index.ts`），`sys_menu` 只决定侧边栏可见性与路由守卫的放行集合
（同文件的 `beforeEach`：当前路径命中不了菜单集合就跳 `/403`）。所以新增业务页必须补一个 D 文件，否则页面点开就是 403：

```sql
INSERT INTO sys_menu (id, parent_id, menu_name, menu_type, path, component, permission, icon, sort, visible)
VALUES (200, 0, '工单管理', 'C', 'ticket', 'business/ticket/index', 'biz:ticket:list', 'ProfileOutlined', 20, 1)
ON CONFLICT (id) DO NOTHING;
INSERT INTO sys_role_menu (role_id, menu_id) SELECT 1, 200 ON CONFLICT DO NOTHING;
SELECT setval('sys_menu_id_seq', (SELECT MAX(id) FROM sys_menu));
```

- 显式写 id（避开基座占用的 1–1072），一个文件一个逻辑变更；补完记得前移 `sys_menu_id_seq`（基座的 id 是写死的，不前移则后台新增菜单必主键冲突）。
- 还要给角色授权（上面的 `sys_role_menu`）：基座只给 admin 角色全量授权过一次，新菜单不补这一行就是「菜单存在但谁也没权」。
- `path` 与路由表同一口径：顶层菜单写片段即可（拼接后是 `/ticket`），子级相对片段会拼上父级完整路径（`system` + `user` → `/system/user`）；
  拼接逻辑在 `frontend/src/utils/menuPath.ts`，侧边栏与守卫共用它，两边不会出现两套口径。
- `menu_type`：`M`=目录、`C`=菜单、`F`=按钮。后端 `/auth/routers` 只下发 M/C，`F` 不进侧边栏，只进 `permissions` 集合，
  供 `useUserStore().hasPermission('biz:ticket:add')` 做按钮级显隐（admin 恒过）。
