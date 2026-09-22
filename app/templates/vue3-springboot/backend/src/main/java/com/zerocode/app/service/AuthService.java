package com.zerocode.app.service;

import com.zerocode.app.dto.LoginDTO;
import com.zerocode.app.dto.PasswordDTO;
import com.zerocode.app.dto.ProfileDTO;
import com.zerocode.app.vo.LoginVO;

/**
 * 认证服务
 */
public interface AuthService {

    /**
     * 登录
     */
    LoginVO login(LoginDTO dto);

    /**
     * 登出
     */
    void logout();

    /**
     * 获取当前登录用户信息
     */
    LoginVO getUserInfo();

    /**
     * 修改当前用户密码
     */
    void updatePassword(PasswordDTO dto);

    /**
     * 修改当前用户个人信息
     */
    void updateProfile(ProfileDTO dto);
}
