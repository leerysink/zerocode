package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.zerocode.app.common.PageResult;
import com.zerocode.app.entity.SysOperLog;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.mapper.SysOperLogMapper;
import com.zerocode.app.mapper.SysUserMapper;
import com.zerocode.app.service.SysOperLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 操作日志服务实现
 */
@Service
@RequiredArgsConstructor
public class SysOperLogServiceImpl implements SysOperLogService {

    private final SysOperLogMapper sysOperLogMapper;
    private final SysUserMapper sysUserMapper;

    /**
     * 分页查询操作日志
     */
    @Override
    public PageResult<SysOperLog> page(Long current, Long size, String title, Integer businessType,
                                       Integer status, String operUserName,
                                       LocalDateTime beginTime, LocalDateTime endTime) {
        LambdaQueryWrapper<SysOperLog> wrapper = new LambdaQueryWrapper<>();
        wrapper.like(StringUtils.hasText(title), SysOperLog::getTitle, title);
        wrapper.eq(businessType != null, SysOperLog::getBusinessType, businessType);
        wrapper.eq(status != null, SysOperLog::getStatus, status);
        // 操作人列存的是名称快照（新数据为昵称、早期数据为登录名），故昵称/登录名任一命中均返回
        if (StringUtils.hasText(operUserName)) {
            List<Long> matchedUserIds = matchedUserIds(operUserName);
            wrapper.and(w -> {
                w.like(SysOperLog::getOperUserName, operUserName);
                if (!matchedUserIds.isEmpty()) {
                    w.or().in(SysOperLog::getOperUserId, matchedUserIds);
                }
            });
        }
        wrapper.ge(beginTime != null, SysOperLog::getCreateTime, beginTime);
        wrapper.le(endTime != null, SysOperLog::getCreateTime, endTime);
        wrapper.orderByDesc(SysOperLog::getCreateTime);
        Page<SysOperLog> page = sysOperLogMapper.selectPage(new Page<>(current, size), wrapper);
        return PageResult.of(page);
    }

    /**
     * 按昵称/登录名模糊匹配到的用户ID
     */
    private List<Long> matchedUserIds(String keyword) {
        LambdaQueryWrapper<SysUser> userWrapper = new LambdaQueryWrapper<>();
        userWrapper.select(SysUser::getId);
        userWrapper.and(w -> w.like(SysUser::getNickname, keyword).or().like(SysUser::getUsername, keyword));
        return sysUserMapper.selectList(userWrapper).stream()
                .map(SysUser::getId)
                .collect(Collectors.toList());
    }

    /**
     * 异步保存操作日志
     */
    @Override
    @Async
    public void asyncSave(SysOperLog operLog) {
        try {
            sysOperLogMapper.insert(operLog);
        } catch (Exception e) {
            // 日志保存失败不影响主业务
        }
    }

    /**
     * 删除操作日志
     */
    @Override
    public void delete(Long id) {
        sysOperLogMapper.deleteById(id);
    }

    /**
     * 清空操作日志
     */
    @Override
    public void clean() {
        sysOperLogMapper.delete(new LambdaQueryWrapper<>());
    }
}
