package com.zerocode.app.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 部门实体
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("sys_dept")
public class SysDept extends BaseEntity {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long parentId;

    /** 祖级路径，形如 0,1, ；仅用于树展示与移动子树，不参与权限 SQL */
    private String ancestors;

    private String deptName;

    private Integer orderNum;

    private String leader;

    private String phone;

    /** 状态: 1=正常 0=停用 */
    private Integer status;
}
