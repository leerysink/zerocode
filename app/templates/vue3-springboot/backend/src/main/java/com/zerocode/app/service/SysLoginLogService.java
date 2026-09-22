package com.zerocode.app.service;

import com.zerocode.app.common.PageResult;
import com.zerocode.app.entity.SysLoginLog;

import java.time.LocalDateTime;

/**
 * 登录日志服务
 */
public interface SysLoginLogService {

    /**
     * 分页查询登录日志
     */
    PageResult<SysLoginLog> page(Long current, Long size, String userName, Integer status, Integer loginType,
                                 String ipaddr, LocalDateTime beginTime, LocalDateTime endTime);

    /**
     * 异步保存登录日志
     */
    void asyncSave(SysLoginLog loginLog);

    /**
     * 删除登录日志
     */
    void delete(Long id);

    /**
     * 清空登录日志
     */
    void clean();
}
