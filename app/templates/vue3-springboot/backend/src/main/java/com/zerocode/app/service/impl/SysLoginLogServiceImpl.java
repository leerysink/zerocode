package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.zerocode.app.common.PageResult;
import com.zerocode.app.entity.SysLoginLog;
import com.zerocode.app.mapper.SysLoginLogMapper;
import com.zerocode.app.service.SysLoginLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;

/**
 * 登录日志服务实现
 */
@Service
@RequiredArgsConstructor
public class SysLoginLogServiceImpl implements SysLoginLogService {

    private final SysLoginLogMapper sysLoginLogMapper;

    /**
     * 分页查询登录日志
     */
    @Override
    public PageResult<SysLoginLog> page(Long current, Long size, String userName, Integer status,
                                        Integer loginType, String ipaddr,
                                        LocalDateTime beginTime, LocalDateTime endTime) {
        LambdaQueryWrapper<SysLoginLog> wrapper = new LambdaQueryWrapper<>();
        wrapper.like(StringUtils.hasText(userName), SysLoginLog::getUserName, userName);
        wrapper.eq(status != null, SysLoginLog::getStatus, status);
        wrapper.eq(loginType != null, SysLoginLog::getLoginType, loginType);
        wrapper.like(StringUtils.hasText(ipaddr), SysLoginLog::getIpaddr, ipaddr);
        wrapper.ge(beginTime != null, SysLoginLog::getLoginTime, beginTime);
        wrapper.le(endTime != null, SysLoginLog::getLoginTime, endTime);
        wrapper.orderByDesc(SysLoginLog::getLoginTime);
        Page<SysLoginLog> page = sysLoginLogMapper.selectPage(new Page<>(current, size), wrapper);
        return PageResult.of(page);
    }

    /**
     * 异步保存登录日志
     */
    @Override
    @Async
    public void asyncSave(SysLoginLog loginLog) {
        try {
            sysLoginLogMapper.insert(loginLog);
        } catch (Exception e) {
            // 日志保存失败不影响主业务
        }
    }

    /**
     * 删除登录日志
     */
    @Override
    public void delete(Long id) {
        sysLoginLogMapper.deleteById(id);
    }

    /**
     * 清空登录日志
     */
    @Override
    public void clean() {
        sysLoginLogMapper.delete(new LambdaQueryWrapper<>());
    }
}
