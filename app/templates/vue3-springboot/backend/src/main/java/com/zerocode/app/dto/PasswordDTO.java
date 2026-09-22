package com.zerocode.app.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

/**
 * 修改密码 DTO
 */
@Data
public class PasswordDTO {

    @NotBlank(message = "旧密码不能为空")
    /** 口令只进不出：与 LoginDTO 同口径，不让新旧密码出现在任何序列化输出里 */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String oldPassword;

    @NotBlank(message = "新密码不能为空")
    @Size(min = 6, max = 32, message = "密码长度应在 6-32 位之间")
    /** 口令只进不出：与 LoginDTO 同口径，不让新旧密码出现在任何序列化输出里 */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String newPassword;
}
