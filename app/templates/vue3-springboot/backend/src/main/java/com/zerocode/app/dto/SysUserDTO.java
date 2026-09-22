package com.zerocode.app.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.List;

/**
 * 用户新增/修改DTO
 */
@Data
public class SysUserDTO {

    private Long id;

    @NotBlank(message = "用户名不能为空")
    private String username;

    /** 只进不出：允许前端传入，但不得被回显或被操作日志的 JSON 序列化带出去 */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String password;

    private String nickname;

    private Long deptId;

    private String phone;

    private String email;

    private Integer status;

    /** 角色ID集合 */
    private List<Long> roleIds;
}
