package com.zerocode.app.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.List;

/**
 * 角色新增/修改DTO
 */
@Data
public class SysRoleDTO {

    private Long id;

    @NotBlank(message = "角色名称不能为空")
    private String roleName;

    @NotBlank(message = "角色权限标识不能为空")
    private String roleKey;

    /** 数据范围: 1=全部 2=本部门 3=本部门及以下 4=自定义 */
    private Integer dataScope;

    private Integer sort;

    private Integer status;

    private String remark;

    /** 菜单ID集合 */
    private List<Long> menuIds;

    /** 自定义数据权限的部门ID集合（dataScope=4 时生效） */
    private List<Long> deptIds;
}
