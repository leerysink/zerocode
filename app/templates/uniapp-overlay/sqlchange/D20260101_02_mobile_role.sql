-- D20260101_02_mobile_role.sql —— 「移动端用户」角色（幂等可重放；只追加不改）
-- 固定日期 20260101：紧随 D20260101_01_rbac_seed.sql，永远排在本应用自行新增的文件之前。
-- 只在移动栈（vue3-springboot-mobile）里存在：H5/APK 的注册接口给新账号挂这个角色。
-- role_key='mobile' 与 SysUserMapper.xml 的默认过滤、MobileAuthServiceImpl.MOBILE_ROLE_KEY 三处对齐，改一处必须同改三处。
-- data_scope=2（本部门）：注册账号 dept_id 为空，可见部门集合因此为空 → 后台数据一律看不到（fail-closed）。
-- 刻意不写 sys_role_menu：这个角色没有任何后台菜单（C 端账号只调业务接口，不进后台）。
INSERT INTO sys_role (role_name, role_key, data_scope, sort, status, remark)
VALUES ('移动端用户', 'mobile', 2, 3, 1, 'H5/APK 注册的 C 端账号，无后台菜单')
ON CONFLICT DO NOTHING;
