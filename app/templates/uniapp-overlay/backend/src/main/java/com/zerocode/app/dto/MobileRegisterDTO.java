package com.zerocode.app.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

/**
 * 移动端注册请求DTO
 *
 * <p>只收用户名、密码、昵称三项：C 端用户不属于企业组织架构，不填部门；
 * 手机号/邮箱留白，后续可由用户在个人中心补全
 */
@Data
public class MobileRegisterDTO {

    @NotBlank(message = "用户名不能为空")
    @Size(min = 3, max = 20, message = "用户名长度需在 3-20 之间")
    @Pattern(regexp = "^[A-Za-z0-9_]+$", message = "用户名只能包含字母、数字和下划线")
    private String username;

    /** 口令只进不出：避免被操作日志 / 响应体 / 缓存意外序列化带出去 */
    @NotBlank(message = "密码不能为空")
    @Size(min = 6, max = 64, message = "密码长度需在 6-64 之间")
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String password;

    @Size(max = 50, message = "昵称最长 50 个字符")
    private String nickname;
}
