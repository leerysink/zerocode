package com.zerocode.app.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * 字典数据DTO
 */
@Data
public class SysDictDataDTO {

    private Long id;

    @NotBlank(message = "字典类型编码不能为空")
    private String dictType;

    @NotBlank(message = "字典标签不能为空")
    private String dictLabel;

    @NotBlank(message = "字典值不能为空")
    private String dictValue;

    private Integer sort;

    private Integer status;

    private String remark;
}
