package com.zerocode.app.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 角色实体
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("sys_role")
public class SysRole extends BaseEntity {

    @TableId(type = IdType.AUTO)
    private Long id;

    private String roleName;

    private String roleKey;

    /** 数据范围: 1=全部 2=本部门 3=本部门及以下 4=自定义 */
    private Integer dataScope;

    private Integer sort;

    private Integer status;

    private String remark;
}
