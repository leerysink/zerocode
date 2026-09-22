package com.zerocode.app.service.impl;

import com.zerocode.app.common.BusinessException;
import com.zerocode.app.common.IpUtils;
import com.zerocode.app.common.LoginUserCache;
import com.zerocode.app.common.SecurityUtils;
import com.zerocode.app.common.ServletUtils;
import com.zerocode.app.common.UserAgentUtils;
import com.zerocode.app.dto.LoginDTO;
import com.zerocode.app.dto.PasswordDTO;
import com.zerocode.app.dto.ProfileDTO;
import com.zerocode.app.entity.SysLoginLog;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.mapper.SysUserMapper;
import com.zerocode.app.security.JwtUtils;
import com.zerocode.app.security.LoginUser;
import com.zerocode.app.security.UserDetailsServiceImpl;
import com.zerocode.app.service.AuthService;
import com.zerocode.app.service.SysLoginLogService;
import com.zerocode.app.vo.LoginVO;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

/**
 * 认证服务实现
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class AuthServiceImpl implements AuthService {

    private final AuthenticationManager authenticationManager;
    private final JwtUtils jwtUtils;
    private final UserDetailsServiceImpl userDetailsService;
    private final PasswordEncoder passwordEncoder;
    private final SysUserMapper sysUserMapper;
    private final SysLoginLogService sysLoginLogService;
    private final LoginUserCache loginUserCache;

    @Override
    public LoginVO login(LoginDTO dto) {
        Authentication authentication;
        try {
            authentication = authenticationManager.authenticate(
                    new UsernamePasswordAuthenticationToken(dto.getUsername(), dto.getPassword()));
        } catch (Exception e) {
            log.warn("登录失败: username={}, msg={}", dto.getUsername(), e.getMessage());
            // 记录登录失败日志
            recordLoginLog(dto.getUsername(), null, 0, "用户名或密码错误", 1);
            throw BusinessException.of(401, "用户名或密码错误");
        }

        LoginUser loginUser = (LoginUser) authentication.getPrincipal();
        cacheLoginUser(loginUser);
        log.info("用户登录成功: {}", dto.getUsername());
        // 记录登录成功日志
        recordLoginLog(loginUser.getUsername(), loginUser.getUserId(), 1, "登录成功", 1);

        return buildLoginVO(loginUser, jwtUtils.createToken(loginUser.getUserId(), loginUser.getUsername()));
    }

    @Override
    public void logout() {
        LoginUser loginUser = SecurityUtils.getLoginUser();
        if (loginUser != null) {
            loginUserCache.remove(loginUser.getUserId());
            log.info("用户登出: {}", loginUser.getUsername());
            // 记录登出日志
            recordLoginLog(loginUser.getUsername(), loginUser.getUserId(), 1, "退出成功", 2);
        }
    }

    /**
     * 记录登录日志
     * @param userName 用户名
     * @param userId 用户ID（登录失败时可能为空）
     * @param status 状态: 0=失败 1=成功
     * @param msg 提示消息
     * @param loginType 日志类型: 1=登录 2=登出
     */
    private void recordLoginLog(String userName, Long userId, int status, String msg, int loginType) {
        try {
            SysLoginLog loginLog = new SysLoginLog();
            loginLog.setUserName(userName);
            loginLog.setUserId(userId);
            loginLog.setStatus(status);
            loginLog.setMsg(msg);
            loginLog.setLoginType(loginType);
            loginLog.setLoginTime(LocalDateTime.now());

            HttpServletRequest request = ServletUtils.getRequest();
            if (request != null) {
                String ip = IpUtils.getIpAddr(request);
                loginLog.setIpaddr(ip);
                loginLog.setLoginLocation(IpUtils.getRealAddressByIP(ip));
                loginLog.setBrowser(UserAgentUtils.getBrowser(request));
                loginLog.setOs(UserAgentUtils.getOs(request));
            }
            sysLoginLogService.asyncSave(loginLog);
        } catch (Exception e) {
            log.warn("记录登录日志失败: {}", e.getMessage());
        }
    }

    @Override
    public LoginVO getUserInfo() {
        LoginUser loginUser = SecurityUtils.getLoginUser();
        if (loginUser == null) {
            throw BusinessException.of(401, "未登录");
        }
        // 重新从DB加载最新数据，保持信息同步
        SysUser user = sysUserMapper.selectById(loginUser.getUserId());
        if (user == null) {
            throw BusinessException.of("用户不存在");
        }
        loginUser.setNickname(user.getNickname());
        loginUser.setDeptId(user.getDeptId());
        loginUser.getSysUser().setNickname(user.getNickname());
        loginUser.getSysUser().setPhone(user.getPhone());
        loginUser.getSysUser().setEmail(user.getEmail());
        // 只刷新仍存在的会话（refresh 内部 EXISTS + SET KEEPTTL）：
        // 此处不重读 roles/permissions，若用 put 会把刚被踢掉的旧快照连同全量 TTL 一起写回
        loginUserCache.refresh(loginUser.getUserId(), loginUser);
        LoginVO vo = new LoginVO();
        vo.setUserId(loginUser.getUserId());
        vo.setUsername(loginUser.getUsername());
        vo.setNickname(loginUser.getNickname());
        vo.setDeptId(loginUser.getDeptId());
        vo.setRoles(loginUser.getRoles());
        vo.setPermissions(loginUser.getPermissions());
        return vo;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void updatePassword(PasswordDTO dto) {
        LoginUser loginUser = SecurityUtils.getLoginUser();
        if (loginUser == null) {
            throw BusinessException.of(401, "未登录");
        }
        // 1. 验证旧密码
        try {
            authenticationManager.authenticate(
                    new UsernamePasswordAuthenticationToken(loginUser.getUsername(), dto.getOldPassword()));
        } catch (BadCredentialsException e) {
            throw BusinessException.of("旧密码错误");
        } catch (Exception e) {
            log.warn("修改密码验证失败: {}", e.getMessage());
            throw BusinessException.of("旧密码错误");
        }

        // 2. 加密新密码并更新
        SysUser update = new SysUser();
        update.setId(loginUser.getUserId());
        update.setPassword(passwordEncoder.encode(dto.getNewPassword()));
        sysUserMapper.updateById(update);
        // 同步 LoginUser 中的密码
        loginUser.setPassword(update.getPassword());
        loginUser.getSysUser().setPassword(update.getPassword());

        // 3. 清除登录缓存，下次访问需重新登录
        loginUserCache.remove(loginUser.getUserId());
        log.info("用户[{}] 修改密码成功，已强制下线", loginUser.getUsername());
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void updateProfile(ProfileDTO dto) {
        LoginUser loginUser = SecurityUtils.getLoginUser();
        if (loginUser == null) {
            throw BusinessException.of(401, "未登录");
        }
        SysUser update = new SysUser();
        update.setId(loginUser.getUserId());
        update.setNickname(dto.getNickname());
        update.setPhone(dto.getPhone());
        update.setEmail(dto.getEmail());
        sysUserMapper.updateById(update);

        // 同步LoginUser中的字段
        loginUser.setNickname(dto.getNickname());
        loginUser.getSysUser().setNickname(dto.getNickname());
        loginUser.getSysUser().setPhone(dto.getPhone());
        loginUser.getSysUser().setEmail(dto.getEmail());
        // 刷新Redis缓存，保持登录状态有效（同样不可重置 TTL / 不可重建已失效会话）
        loginUserCache.refresh(loginUser.getUserId(), loginUser);
        log.info("用户[{}] 更新个人信息成功", loginUser.getUsername());
    }

    private void cacheLoginUser(LoginUser loginUser) {
        loginUserCache.put(loginUser.getUserId(), loginUser);
    }

    private LoginVO buildLoginVO(LoginUser loginUser, String token) {
        LoginVO vo = new LoginVO();
        if (token != null) {
            vo.setToken(token);
        }
        vo.setUserId(loginUser.getUserId());
        vo.setUsername(loginUser.getUsername());
        vo.setNickname(loginUser.getNickname());
        vo.setDeptId(loginUser.getDeptId());
        vo.setRoles(loginUser.getRoles());
        vo.setPermissions(loginUser.getPermissions());
        return vo;
    }
}
