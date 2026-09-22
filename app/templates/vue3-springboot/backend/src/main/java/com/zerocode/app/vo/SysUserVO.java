package com.zerocode.app.vo;

import com.fasterxml.jackson.annotation.JsonFormat;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 用户列表VO（含所属部门名称）
 */
@Data
public class SysUserVO {

    private Long id;

    private String username;

    private String nickname;

    /** 所属部门ID，未绑定为 null */
    private Long deptId;

    /** 所属部门名称，左连接 sys_dept 且不过滤逻辑删除，历史/已删部门同样可显示 */
    private String deptName;

    private String phone;

    private String email;

    /** 状态: 1=正常 0=停用 */
    private Integer status;

    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime createTime;
}