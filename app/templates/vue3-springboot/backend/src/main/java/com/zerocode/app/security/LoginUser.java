package com.zerocode.app.security;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.zerocode.app.entity.SysUser;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;

import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 登录用户身份信息
 */
@Data
@NoArgsConstructor
public class LoginUser implements UserDetails {

    private Long userId;
    private String username;
    /** 只进不出：不把口令（哪怕是哈希）写进 Redis 登录态 */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String password;
    private String nickname;
    private Long deptId;
    private Integer status;
    private Set<String> roles;
    private Set<String> permissions;
    private SysUser sysUser;

    public LoginUser(SysUser sysUser, Set<String> roles, Set<String> permissions) {
        this.sysUser = sysUser;
        this.userId = sysUser.getId();
        this.username = sysUser.getUsername();
        this.password = sysUser.getPassword();
        this.nickname = sysUser.getNickname();
        this.deptId = sysUser.getDeptId();
        this.status = sysUser.getStatus();
        this.roles = roles;
        this.permissions = permissions;
    }

    @JsonIgnore
    @Override
    public Collection<? extends GrantedAuthority> getAuthorities() {
        Set<GrantedAuthority> authorities = new HashSet<>();
        roles.forEach(role -> authorities.add(new SimpleGrantedAuthority("ROLE_" + role)));
        permissions.forEach(perm -> authorities.add(new SimpleGrantedAuthority(perm)));
        return authorities;
    }

    @JsonIgnore
    @Override
    public String getPassword() {
        return password;
    }

    @Override
    public String getUsername() {
        return username;
    }

    @JsonIgnore
    @Override
    public boolean isAccountNonExpired() {
        return true;
    }

    @JsonIgnore
    @Override
    public boolean isAccountNonLocked() {
        return true;
    }

    @JsonIgnore
    @Override
    public boolean isCredentialsNonExpired() {
        return true;
    }

    @JsonIgnore
    @Override
    public boolean isEnabled() {
        return status != null && status == 1;
    }
}
