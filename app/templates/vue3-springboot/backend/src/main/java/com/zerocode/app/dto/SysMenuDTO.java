package com.zerocode.app.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * 菜单新增/修改DTO
 */
@Data
public class SysMenuDTO {

    private Long id;

    private Long parentId;

    @NotBlank(message = "菜单名称不能为空")
    private String menuName;

    /** 菜单类型: M=目录 C=菜单 F=按钮 */
    @NotBlank(message = "菜单类型不能为空")
    private String menuType;

    private String path;

    private String component;

    private String permission;

    private String icon;

    private Integer sort;

    private Integer visible;

    private Integer status;
}
