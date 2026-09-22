package com.zerocode.app.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * 字典类型DTO
 */
@Data
public class SysDictTypeDTO {

    private Long id;

    @NotBlank(message = "字典名称不能为空")
    private String dictName;

    @NotBlank(message = "字典类型编码不能为空")
    private String dictType;

    private Integer status;

    private String remark;
}
