package com.zerocode.app.common;

/**
 * 系统常量
 */
public class Constants {

    private Constants() {
    }

    /** 数据范围: 全部 */
    public static final int DATA_SCOPE_ALL = 1;
    /** 数据范围: 本部门 */
    public static final int DATA_SCOPE_DEPT = 2;
    /** 数据范围: 本部门及以下 */
    public static final int DATA_SCOPE_DEPT_AND_CHILD = 3;
    /** 数据范围: 自定义 */
    public static final int DATA_SCOPE_CUSTOM = 4;

    /** 状态: 正常 */
    public static final int STATUS_NORMAL = 1;
    /** 状态: 停用 */
    public static final int STATUS_DISABLED = 0;

    /** 超级管理员角色key */
    public static final String ROLE_ADMIN = "admin";

    /** Redis 登录用户 key 前缀（后接用户ID）；v2 = 登录态载荷结构升版号，字段改名时递增 */
    public static final String REDIS_LOGIN_USER_PREFIX = "login:user:v2:";

    /** 空数据范围哨兵 id：可见部门集合为空时占位，保证 IN 语法合法且匹配不到任何数据 */
    public static final Long EMPTY_DATA_SCOPE_SENTINEL = -1L;

    /** 菜单类型: 目录 */
    public static final String MENU_TYPE_DIR = "M";
    /** 菜单类型: 菜单 */
    public static final String MENU_TYPE_MENU = "C";
    /** 菜单类型: 按钮 */
    public static final String MENU_TYPE_BUTTON = "F";
}
