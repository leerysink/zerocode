package com.zerocode.app.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonFormat;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 菜单实体
 */
@Data
@TableName("sys_menu")
public class SysMenu implements Serializable {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long parentId;

    private String menuName;

    /** 菜单类型: M=目录 C=菜单 F=按钮 */
    private String menuType;

    private String path;

    private String component;

    private String permission;

    private String icon;

    private Integer sort;

    private Integer visible;

    private Integer status;

    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime createTime;
}
