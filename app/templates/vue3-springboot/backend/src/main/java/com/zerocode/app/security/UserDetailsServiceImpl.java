package com.zerocode.app.security;

import com.zerocode.app.common.BusinessException;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.mapper.SysUserMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 用户认证服务实现
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class UserDetailsServiceImpl implements UserDetailsService {

    private final SysUserMapper sysUserMapper;

    @Override
    public UserDetails loadUserByUsername(String username) throws UsernameNotFoundException {
        SysUser user = sysUserMapper.selectByUsername(username);
        if (user == null) {
            throw new UsernameNotFoundException("用户不存在: " + username);
        }
        if (user.getStatus() != null && user.getStatus() == 0) {
            throw new BusinessException("账号已被停用，请联系管理员");
        }

        List<String> roleKeys = sysUserMapper.selectRoleKeysByUserId(user.getId());
        List<String> permissions = sysUserMapper.selectPermissionsByUserId(user.getId());

        Set<String> roles = new HashSet<>(roleKeys);
        Set<String> perms = new HashSet<>(permissions);

        log.debug("加载用户: {}, 角色: {}, 权限数: {}", username, roles, perms.size());
        return new LoginUser(user, roles, perms);
    }
}
