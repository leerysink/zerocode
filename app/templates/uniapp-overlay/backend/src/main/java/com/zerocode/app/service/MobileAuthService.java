package com.zerocode.app.service;

import com.zerocode.app.dto.MobileRegisterDTO;

/**
 * 移动端账号服务
 */
public interface MobileAuthService {

    /**
     * 注册一个只挂「移动端用户」角色的 C 端账号
     */
    void register(MobileRegisterDTO dto);
}
