package com.zerocode.app.service;

import com.zerocode.app.common.PageResult;
import com.zerocode.app.entity.SysOperLog;

import java.time.LocalDateTime;

/**
 * 操作日志服务
 */
public interface SysOperLogService {

    /**
     * 分页查询操作日志
     *
     * <p>operUserName 按操作人关键字模糊查询，名称快照列与关联用户的昵称/登录名任一命中即返回。
     */
    PageResult<SysOperLog> page(Long current, Long size, String title, Integer businessType, Integer status,
                                String operUserName, LocalDateTime beginTime, LocalDateTime endTime);

    /**
     * 异步保存操作日志
     */
    void asyncSave(SysOperLog operLog);

    /**
     * 删除操作日志
     */
    void delete(Long id);

    /**
     * 清空操作日志
     */
    void clean();
}
