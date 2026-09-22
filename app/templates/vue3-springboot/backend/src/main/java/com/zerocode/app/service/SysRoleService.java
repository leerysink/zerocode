package com.zerocode.app.service;

import com.zerocode.app.common.PageResult;
import com.zerocode.app.dto.SysRoleDTO;
import com.zerocode.app.entity.SysRole;
import com.zerocode.app.vo.RoleMemberVO;

import java.util.List;

/**
 * 角色服务
 */
public interface SysRoleService {

    PageResult<SysRole> page(Long current, Long size, String roleName);

    List<SysRole> listAll();

    SysRole getById(Long id);

    /**
     * 查询角色关联的菜单ID
     */
    List<Long> getMenuIds(Long roleId);

    /**
     * 查询角色关联的部门ID（自定义数据权限）
     */
    List<Long> getDeptIds(Long roleId);

    /**
     * 查询用户所有角色授权的部门ID
     */
    List<Long> getUserDeptIds(Long userId);

    /**
     * 查询角色已授权的成员（含停用用户）
     */
    List<RoleMemberVO> members(Long roleId);

    /**
     * 查询可授权给该角色的启用用户
     */
    List<RoleMemberVO> candidateMembers(Long roleId);

    /**
     * 为角色批量授权用户，已授权的自动跳过
     */
    void addMembers(Long roleId, List<Long> userIds);

    /**
     * 解除用户与角色的关联
     */
    void removeMember(Long roleId, Long userId);

    void add(SysRoleDTO dto);

    void update(SysRoleDTO dto);

    void delete(Long id);
}
