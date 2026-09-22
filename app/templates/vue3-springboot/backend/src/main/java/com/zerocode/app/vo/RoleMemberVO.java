package com.zerocode.app.vo;

import lombok.Data;

/**
 * 角色成员（人员管理弹窗列表项）
 */
@Data
public class RoleMemberVO {

    private Long userId;

    private String username;

    private String nickname;

    private String phone;

    private Long deptId;

    private String deptName;

    /** 状态: 1=正常 0=停用 */
    private Integer status;
}
