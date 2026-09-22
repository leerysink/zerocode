package com.zerocode.app.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * 部门新增/修改DTO
 */
@Data
public class SysDeptDTO {

    private Long id;

    /** 上级部门ID，顶级部门传 0 */
    @NotNull(message = "上级部门不能为空")
    private Long parentId;

    @NotBlank(message = "部门名称不能为空")
    private String deptName;

    private Integer orderNum;

    private String leader;

    private String phone;

    private Integer status;
}
