package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.zerocode.app.common.BusinessException;
import com.zerocode.app.common.Constants;
import com.zerocode.app.common.LoginUserCache;
import com.zerocode.app.common.PageResult;
import com.zerocode.app.dto.SysRoleDTO;
import com.zerocode.app.entity.SysRole;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.mapper.SysMenuMapper;
import com.zerocode.app.mapper.SysRoleMapper;
import com.zerocode.app.mapper.SysUserMapper;
import com.zerocode.app.service.SysRoleService;
import com.zerocode.app.vo.RoleMemberVO;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashSet;
import java.util.List;

/**
 * 角色服务实现
 */
@Service
@RequiredArgsConstructor
public class SysRoleServiceImpl implements SysRoleService {

    private final SysRoleMapper sysRoleMapper;
    private final SysMenuMapper sysMenuMapper;
    private final SysUserMapper sysUserMapper;
    private final LoginUserCache loginUserCache;

    @Override
    public PageResult<SysRole> page(Long current, Long size, String roleName) {
        LambdaQueryWrapper<SysRole> wrapper = new LambdaQueryWrapper<>();
        wrapper.like(roleName != null && !roleName.isEmpty(), SysRole::getRoleName, roleName);
        wrapper.orderByAsc(SysRole::getSort);
        Page<SysRole> page = sysRoleMapper.selectPage(new Page<>(current, size), wrapper);
        return PageResult.of(page);
    }

    @Override
    public List<SysRole> listAll() {
        LambdaQueryWrapper<SysRole> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysRole::getStatus, 1);
        wrapper.orderByAsc(SysRole::getSort);
        return sysRoleMapper.selectList(wrapper);
    }

    @Override
    public SysRole getById(Long id) {
        SysRole role = sysRoleMapper.selectById(id);
        if (role == null) {
            throw BusinessException.of("角色不存在");
        }
        return role;
    }

    /**
     * 查询角色关联的菜单ID
     */
    @Override
    public List<Long> getMenuIds(Long roleId) {
        return sysMenuMapper.selectMenuIdsByRoleId(roleId);
    }

    /**
     * 查询角色关联的部门ID（自定义数据权限）
     */
    @Override
    public List<Long> getDeptIds(Long roleId) {
        return sysRoleMapper.selectDeptIdsByRoleId(roleId);
    }

    /**
     * 查询用户所有角色授权的部门ID
     */
    @Override
    public List<Long> getUserDeptIds(Long userId) {
        return sysRoleMapper.selectDeptIdsByUserId(userId);
    }

    /**
     * 查询角色已授权的成员（含停用用户）
     */
    @Override
    public List<RoleMemberVO> members(Long roleId) {
        getById(roleId);
        return sysRoleMapper.selectMembersByRoleId(roleId);
    }

    /**
     * 查询可授权给该角色的启用用户
     */
    @Override
    public List<RoleMemberVO> candidateMembers(Long roleId) {
        getById(roleId);
        return sysRoleMapper.selectCandidateMembersByRoleId(roleId);
    }

    /**
     * 为角色批量授权用户，已授权的自动跳过
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public void addMembers(Long roleId, List<Long> userIds) {
        getById(roleId);
        if (userIds == null || userIds.isEmpty()) {
            throw BusinessException.of("请选择要授权的用户");
        }
        List<SysUser> users = sysUserMapper.selectBatchIds(userIds);
        if (users.size() != new HashSet<>(userIds).size()) {
            throw BusinessException.of("存在无效用户，请刷新后重试");
        }
        if (users.stream().anyMatch(u -> u.getStatus() == null || u.getStatus() != Constants.STATUS_NORMAL)) {
            throw BusinessException.of("只能授权启用状态的用户");
        }
        List<Long> boundIds = sysRoleMapper.selectMemberUserIdsByRoleId(roleId);
        List<Long> toInsert = userIds.stream().distinct().filter(id -> !boundIds.contains(id)).toList();
        if (toInsert.isEmpty()) {
            return;
        }
        sysRoleMapper.batchInsertUserRoleByRoleId(roleId, toInsert);
        // 新授权的用户需要重新登录，否则拿的还是旧角色算出的权限
        loginUserCache.removeByUserIds(toInsert);
    }

    /**
     * 解除用户与角色的关联
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public void removeMember(Long roleId, Long userId) {
        SysRole role = getById(roleId);
        List<Long> boundIds = sysRoleMapper.selectMemberUserIdsByRoleId(roleId);
        if (!boundIds.contains(userId)) {
            return;
        }
        if (Constants.ROLE_ADMIN.equals(role.getRoleKey()) && boundIds.size() <= 1) {
            throw BusinessException.of("超级管理员角色至少保留一名成员，无法移除");
        }
        sysRoleMapper.deleteUserRoleByRoleIdAndUserId(roleId, userId);
        // 解除授权后立即踢线，不让该用户继续持有这个角色的权限
        loginUserCache.remove(userId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void add(SysRoleDTO dto) {
        SysRole role = new SysRole();
        BeanUtils.copyProperties(dto, role);
        if (role.getDataScope() == null) {
            role.setDataScope(Constants.DATA_SCOPE_DEPT);
        }
        if (role.getStatus() == null) {
            role.setStatus(1);
        }
        if (role.getSort() == null) {
            role.setSort(0);
        }
        sysRoleMapper.insert(role);
        saveRoleRelations(role.getId(), dto);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void update(SysRoleDTO dto) {
        if (dto.getId() == null) {
            throw BusinessException.of("角色ID不能为空");
        }
        SysRole role = new SysRole();
        BeanUtils.copyProperties(dto, role);
        sysRoleMapper.updateById(role);
        saveRoleRelations(dto.getId(), dto);
    }

    private void saveRoleRelations(Long roleId, SysRoleDTO dto) {
        // 保存角色-菜单关联
        sysRoleMapper.deleteRoleMenuByRoleId(roleId);
        if (dto.getMenuIds() != null && !dto.getMenuIds().isEmpty()) {
            sysRoleMapper.batchInsertRoleMenu(roleId, dto.getMenuIds());
        }
        // 保存角色-部门关联（仅自定义数据范围时）
        sysRoleMapper.deleteRoleDeptByRoleId(roleId);
        if (dto.getDataScope() != null && dto.getDataScope() == Constants.DATA_SCOPE_CUSTOM
                && dto.getDeptIds() != null && !dto.getDeptIds().isEmpty()) {
            sysRoleMapper.batchInsertRoleDept(roleId, dto.getDeptIds());
        }
        // 菜单/数据范围变更后刷新登录态，避免旧权限继续生效
        loginUserCache.removeByUserIds(sysRoleMapper.selectMemberUserIdsByRoleId(roleId));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void delete(Long id) {
        // 先取成员再删关联：删完之后再查已经查不到人，缓存就清不掉了
        List<Long> memberIds = sysRoleMapper.selectMemberUserIdsByRoleId(id);
        sysRoleMapper.deleteRoleMenuByRoleId(id);
        sysRoleMapper.deleteRoleDeptByRoleId(id);
        sysRoleMapper.deleteUserRoleByRoleId(id);
        sysRoleMapper.deleteById(id);
        // 角色停用/删除同样要刷新其成员登录态
        loginUserCache.removeByUserIds(memberIds);
    }
}
