package com.zerocode.app.dto;

import jakarta.validation.constraints.Size;
import lombok.Data;

/**
 * 个人信息修改 DTO（用户只能修改自身昵称、手机号、邮箱、头像）
 */
@Data
public class ProfileDTO {

    @Size(max = 50, message = "昵称长度不能超过50")
    private String nickname;

    @Size(max = 20, message = "手机号长度不能超过20")
    private String phone;

    @Size(max = 50, message = "邮箱长度不能超过50")
    private String email;
}
