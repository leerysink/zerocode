package com.zerocode.app.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * 登录请求DTO
 */
@Data
public class LoginDTO {

    @NotBlank(message = "用户名不能为空")
    private String username;

    /** 口令只进不出：避免被操作日志 / 响应体 / 缓存意外序列化带出去 */
    @NotBlank(message = "密码不能为空")
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String password;
}
