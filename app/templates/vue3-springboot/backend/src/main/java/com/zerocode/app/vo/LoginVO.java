package com.zerocode.app.vo;

import lombok.Data;

import java.util.Set;

/**
 * 登录响应VO
 */
@Data
public class LoginVO {

    private String token;

    private Long userId;

    private String username;

    private String nickname;

    private Long deptId;

    private Set<String> roles;

    private Set<String> permissions;
}
