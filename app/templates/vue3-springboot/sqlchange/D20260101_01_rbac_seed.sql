-- D20260101_01_rbac_seed.sql —— 基座 RBAC 种子（幂等可重放；一经应用不得修改，只能追加新 D 文件）
-- 固定日期 20260101：文件名不经占位替换（渲染器只改内容不改路径），保证永远排在本应用新增文件之前。
-- =====================================================
-- 预置数据
-- =====================================================

-- 部门：三级树中的两层，admin 挂在「研发中心」以便验证 data_scope=2/3 的差异
INSERT INTO sys_dept (id, parent_id, ancestors, dept_name, order_num, leader) VALUES
(1, 0, '0',     '总公司',       0, '管理员'),
(2, 1, '0,1,',  '研发中心',     1, NULL),
(3, 1, '0,1,',  '实施服务部',   2, NULL)
ON CONFLICT (id) DO NOTHING;

-- 超级管理员（密码 admin123 的 BCrypt 哈希，与源项目 init-all.sql 一致）
INSERT INTO sys_user (id, username, password, nickname, dept_id, status) VALUES
(1, 'admin', '$2a$10$7JB720yubVSZvUI0rEqK/.VqGOZTH.ulu33dHOiBE8ByOhJIrdAu2', '超级管理员', 2, 1)
ON CONFLICT (id) DO NOTHING;

-- 角色：data_scope 采用本项目新语义（1=全部 2=本部门 3=本部门及以下 4=自定义）
INSERT INTO sys_role (id, role_name, role_key, data_scope, sort, status, remark) VALUES
(1, '超级管理员', 'admin',  1, 1, 1, '拥有全部菜单与全部部门数据权限'),
(2, '普通用户',   'common', 2, 2, 1, '仅本部门数据，只开放部门与字典维护')
ON CONFLICT (id) DO NOTHING;

INSERT INTO sys_user_role (user_id, role_id) VALUES (1, 1) ON CONFLICT DO NOTHING;

-- 菜单：目录与菜单（侧边栏只渲染 parent_id=0 且 menu_type in ('M','C') 的记录）
INSERT INTO sys_menu (id, parent_id, menu_name, menu_type, path, component, permission, icon, sort, visible) VALUES
(1,   0,   '首页',     'C', '/dashboard', 'dashboard/index',       NULL,                  'HomeOutlined',      0, 1),
(2,   0,   '个人中心', 'C', '/profile',   'profile/index',         NULL,                  'UserOutlined',      9, 1),
(100, 0,   '系统管理', 'M', 'system',     NULL,                    NULL,                  'SettingOutlined', 100, 1),
(101, 100, '用户管理', 'C', 'user',       'system/user/index',     'system:user:list',    'UserOutlined',      1, 1),
(102, 100, '角色管理', 'C', 'role',       'system/role/index',     'system:role:list',    'TeamOutlined',      2, 1),
(103, 100, '菜单管理', 'C', 'menu',       'system/menu/index',     'system:menu:list',    'MenuOutlined',      3, 1),
(104, 100, '部门管理', 'C', 'dept',       'system/dept/index',     'system:dept:list',    'ApartmentOutlined', 4, 1),
(105, 100, '字典管理', 'C', 'dict',       'system/dict/index',     'system:dict:list',    'BookOutlined',      5, 1),
(106, 100, '操作日志', 'C', 'operLog',    'system/operLog/index',  'system:operLog:list', 'FileTextOutlined',  6, 1),
(107, 100, '登录日志', 'C', 'loginLog',   'system/loginLog/index', 'system:loginLog:list','LoginOutlined',     7, 1)
ON CONFLICT (id) DO NOTHING;

-- 菜单：按钮权限（只进前端权限集合，不进侧边栏）
INSERT INTO sys_menu (id, parent_id, menu_name, menu_type, path, component, permission, icon, sort, visible) VALUES
(1011, 101, '用户新增', 'F', NULL, NULL, 'system:user:add',       NULL, 1, 1),
(1012, 101, '用户修改', 'F', NULL, NULL, 'system:user:edit',      NULL, 2, 1),
(1013, 101, '用户删除', 'F', NULL, NULL, 'system:user:delete',    NULL, 3, 1),
(1014, 101, '重置密码', 'F', NULL, NULL, 'system:user:resetPwd',  NULL, 4, 1),
(1021, 102, '角色新增', 'F', NULL, NULL, 'system:role:add',       NULL, 1, 1),
(1022, 102, '角色修改', 'F', NULL, NULL, 'system:role:edit',      NULL, 2, 1),
(1023, 102, '角色删除', 'F', NULL, NULL, 'system:role:delete',    NULL, 3, 1),
(1031, 103, '菜单新增', 'F', NULL, NULL, 'system:menu:add',       NULL, 1, 1),
(1032, 103, '菜单修改', 'F', NULL, NULL, 'system:menu:edit',      NULL, 2, 1),
(1033, 103, '菜单删除', 'F', NULL, NULL, 'system:menu:delete',    NULL, 3, 1),
(1041, 104, '部门新增', 'F', NULL, NULL, 'system:dept:add',       NULL, 1, 1),
(1042, 104, '部门修改', 'F', NULL, NULL, 'system:dept:edit',      NULL, 2, 1),
(1043, 104, '部门删除', 'F', NULL, NULL, 'system:dept:delete',    NULL, 3, 1),
(1051, 105, '字典新增', 'F', NULL, NULL, 'system:dict:add',       NULL, 1, 1),
(1052, 105, '字典修改', 'F', NULL, NULL, 'system:dict:edit',      NULL, 2, 1),
(1053, 105, '字典删除', 'F', NULL, NULL, 'system:dict:delete',    NULL, 3, 1),
(1061, 106, '日志删除', 'F', NULL, NULL, 'system:operLog:delete', NULL, 1, 1),
(1062, 106, '日志清空', 'F', NULL, NULL, 'system:operLog:clean',  NULL, 2, 1),
(1071, 107, '登录日志删除', 'F', NULL, NULL, 'system:loginLog:delete', NULL, 1, 1),
(1072, 107, '登录日志清空', 'F', NULL, NULL, 'system:loginLog:clean',  NULL, 2, 1)
ON CONFLICT (id) DO NOTHING;

-- 角色-菜单：admin 全量，common 仅首页/个人中心/系统管理/部门/字典
INSERT INTO sys_role_menu (role_id, menu_id)
SELECT 1, id FROM sys_menu
ON CONFLICT DO NOTHING;
INSERT INTO sys_role_menu (role_id, menu_id) VALUES
(2, 1), (2, 2), (2, 100), (2, 104), (2, 1041), (2, 1042), (2, 1043), (2, 105), (2, 1051), (2, 1052), (2, 1053)
ON CONFLICT DO NOTHING;

-- 样例字典（证明字典管理开箱可用；源项目的业务字典不迁移）
INSERT INTO sys_dict_type (id, dict_name, dict_type, remark) VALUES
(1, '系统是否', 'sys_yes_no', '通用是否选项')
ON CONFLICT (id) DO NOTHING;
INSERT INTO sys_dict_data (id, dict_type, dict_label, dict_value, sort) VALUES
(1, 'sys_yes_no', '是', 'Y', 1),
(2, 'sys_yes_no', '否', 'N', 2)
ON CONFLICT (id) DO NOTHING;

-- =====================================================
-- 重要：预置数据显式指定了主键 id，PG 的 identity 序列不会自动前移，
-- 必须重置序列，否则后台新增记录会直接主键冲突。
-- （自增主键迁移到 PG identity 最容易漏的一处）
-- 只对「有显式 id 预置数据」的 6 张表重置；两张日志表无预置数据（三张关联表根本没有 id 列），
-- 序列本身就停在起点，不重置也不要重置成 0 —— setval 对 MINVALUE 1 的序列传 0 会报错。
-- =====================================================
SELECT setval('sys_dept_id_seq',      (SELECT MAX(id) FROM sys_dept));
SELECT setval('sys_user_id_seq',      (SELECT MAX(id) FROM sys_user));
SELECT setval('sys_role_id_seq',      (SELECT MAX(id) FROM sys_role));
SELECT setval('sys_menu_id_seq',      (SELECT MAX(id) FROM sys_menu));
SELECT setval('sys_dict_type_id_seq', (SELECT MAX(id) FROM sys_dict_type));
SELECT setval('sys_dict_data_id_seq', (SELECT MAX(id) FROM sys_dict_data));

