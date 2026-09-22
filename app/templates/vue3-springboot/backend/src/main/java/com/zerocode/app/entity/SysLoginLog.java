package com.zerocode.app.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonFormat;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 登录日志实体
 */
@Data
@TableName("sys_login_log")
public class SysLoginLog implements Serializable {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 登录账号 */
    private String userName;

    /** 用户ID */
    private Long userId;

    /** 登录IP地址 */
    private String ipaddr;

    /** 登录地点 */
    private String loginLocation;

    /** 浏览器类型 */
    private String browser;

    /** 操作系统 */
    private String os;

    /** 登录状态: 0=失败 1=成功 */
    private Integer status;

    /** 提示消息 */
    private String msg;

    /** 日志类型: 1=登录 2=登出 */
    private Integer loginType;

    /** 登录时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime loginTime;
}
