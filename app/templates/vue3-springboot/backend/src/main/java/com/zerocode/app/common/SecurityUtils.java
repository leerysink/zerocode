package com.zerocode.app.common;

import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

import com.zerocode.app.security.LoginUser;

/**
 * 安全上下文工具类，获取当前登录用户
 */
public class SecurityUtils {

    private SecurityUtils() {
    }

    /**
     * 获取当前登录用户
     */
    public static LoginUser getLoginUser() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication != null && authentication.getPrincipal() instanceof LoginUser loginUser) {
            return loginUser;
        }
        return null;
    }

    /**
     * 获取当前登录用户ID
     */
    public static Long getUserId() {
        LoginUser loginUser = getLoginUser();
        return loginUser != null ? loginUser.getUserId() : null;
    }

    /**
     * 获取当前登录用户所属部门ID
     */
    public static Long getDeptId() {
        LoginUser loginUser = getLoginUser();
        return loginUser != null ? loginUser.getDeptId() : null;
    }

    /**
     * 获取当前登录用户名
     */
    public static String getUsername() {
        LoginUser loginUser = getLoginUser();
        return loginUser != null ? loginUser.getUsername() : null;
    }

    /**
     * 获取当前登录用户昵称
     */
    public static String getNickname() {
        LoginUser loginUser = getLoginUser();
        return loginUser != null ? loginUser.getNickname() : null;
    }

    /**
     * 是否超级管理员
     */
    public static boolean isAdmin() {
        LoginUser loginUser = getLoginUser();
        return loginUser != null && loginUser.getRoles().contains(Constants.ROLE_ADMIN);
    }

    /**
     * 当前登录用户是否拥有指定角色（按 role_key 判断）
     */
    public static boolean hasRole(String roleKey) {
        LoginUser loginUser = getLoginUser();
        return loginUser != null && loginUser.getRoles() != null && loginUser.getRoles().contains(roleKey);
    }
}
