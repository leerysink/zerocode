package com.zerocode.app.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonFormat;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 操作日志实体
 */
@Data
@TableName("sys_oper_log")
public class SysOperLog implements Serializable {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 模块标题 */
    private String title;

    /** 业务类型: 0=其他 1=新增 2=修改 3=删除 4=授权 5=导出 */
    private Integer businessType;

    /** 方法名称 */
    private String method;

    /** 请求方式 */
    private String requestMethod;

    /** 请求URL */
    private String operUrl;

    /** 主机IP */
    private String operIp;

    /** 操作地点 */
    private String operLocation;

    /** 请求参数 */
    private String operParam;

    /** 返回结果 */
    private String jsonResult;

    /** 操作状态: 0=异常 1=正常 */
    private Integer status;

    /** 错误消息 */
    private String errorMsg;

    /** 操作人员ID */
    private Long operUserId;

    /** 操作人员名称（昵称快照，昵称为空时回退登录名） */
    private String operUserName;

    /** 消耗时间(毫秒) */
    private Long costTime;

    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime createTime;
}
