package com.zerocode.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.zerocode.app.entity.SysLoginLog;
import org.apache.ibatis.annotations.Mapper;

/**
 * 登录日志Mapper
 */
@Mapper
public interface SysLoginLogMapper extends BaseMapper<SysLoginLog> {
}
