package com.zerocode.app.dto;

import jakarta.validation.constraints.NotEmpty;
import lombok.Data;

import java.util.List;

/**
 * 角色授权用户DTO
 */
@Data
public class RoleMemberBindDTO {

    /** 待授权到该角色的用户ID集合 */
    @NotEmpty(message = "请选择要授权的用户")
    private List<Long> userIds;
}
