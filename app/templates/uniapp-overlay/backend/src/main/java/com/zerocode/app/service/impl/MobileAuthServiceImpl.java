package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.zerocode.app.common.BusinessException;
import com.zerocode.app.dto.MobileRegisterDTO;
import com.zerocode.app.entity.SysRole;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.mapper.SysRoleMapper;
import com.zerocode.app.mapper.SysUserMapper;
import com.zerocode.app.service.MobileAuthService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.List;

/**
 * 移动端账号服务实现
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MobileAuthServiceImpl implements MobileAuthService {

    /**
     * 与 sqlchange/D20260101_02_mobile_role.sql 的 role_key、
     * SysUserMapper.xml 里「默认排除移动端用户」的常量三处对齐，改一处必须同改三处
     */
    static final String MOBILE_ROLE_KEY = "mobile";

    private final SysUserMapper sysUserMapper;
    private final SysRoleMapper sysRoleMapper;
    private final PasswordEncoder passwordEncoder;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void register(MobileRegisterDTO dto) {
        if (sysUserMapper.selectByUsername(dto.getUsername()) != null) {
            throw BusinessException.of("用户名已被占用");
        }
        SysRole role = sysRoleMapper.selectOne(
                new LambdaQueryWrapper<SysRole>().eq(SysRole::getRoleKey, MOBILE_ROLE_KEY));
        if (role == null) {
            // 角色由 sqlchange 种子提供；缺了说明本应用的迁移没跑全，注册不能静默建出一个无角色账号
            throw BusinessException.of("移动端角色缺失，请检查数据库初始化");
        }

        SysUser user = new SysUser();
        user.setUsername(dto.getUsername());
        user.setPassword(passwordEncoder.encode(dto.getPassword()));
        user.setNickname(StringUtils.hasText(dto.getNickname()) ? dto.getNickname() : dto.getUsername());
        // 不分配部门：C 端用户不在组织架构里；dept_id 为空也让数据范围计算对其 fail-closed
        user.setStatus(1);
        try {
            sysUserMapper.insert(user);
        } catch (DuplicateKeyException e) {
            // 并发注册同一用户名时唯一索引兜底，先查后插之间仍有窗口
            throw BusinessException.of("用户名已被占用");
        }
        sysRoleMapper.batchInsertUserRole(user.getId(), List.of(role.getId()));
        log.info("移动端用户注册成功: {}", dto.getUsername());
    }
}
