package com.zerocode.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.zerocode.app.entity.SysRole;
import com.zerocode.app.vo.RoleMemberVO;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 角色Mapper
 */
@Mapper
public interface SysRoleMapper extends BaseMapper<SysRole> {

    /**
     * 根据用户ID查询角色
     */
    List<SysRole> selectRolesByUserId(@Param("userId") Long userId);

    /**
     * 根据角色ID查询关联的部门ID
     */
    List<Long> selectDeptIdsByRoleId(@Param("roleId") Long roleId);

    /**
     * 根据用户ID查询所有角色授权的部门ID
     */
    List<Long> selectDeptIdsByUserId(@Param("userId") Long userId);

    /**
     * 删除角色-部门关联
     */
    int deleteRoleDeptByRoleId(@Param("roleId") Long roleId);

    /**
     * 批量插入角色-部门关联
     */
    int batchInsertRoleDept(@Param("roleId") Long roleId, @Param("deptIds") List<Long> deptIds);

    /**
     * 删除用户-角色关联
     */
    int deleteUserRoleByUserId(@Param("userId") Long userId);

    /**
     * 批量插入用户-角色关联
     */
    int batchInsertUserRole(@Param("userId") Long userId, @Param("roleIds") List<Long> roleIds);

    /**
     * 删除角色-菜单关联
     */
    int deleteRoleMenuByRoleId(@Param("roleId") Long roleId);

    /**
     * 批量插入角色-菜单关联
     */
    int batchInsertRoleMenu(@Param("roleId") Long roleId, @Param("menuIds") List<Long> menuIds);

    /**
     * 查询角色已授权的用户列表（含停用用户）
     */
    List<RoleMemberVO> selectMembersByRoleId(@Param("roleId") Long roleId);

    /**
     * 查询未授权该角色的启用用户列表
     */
    List<RoleMemberVO> selectCandidateMembersByRoleId(@Param("roleId") Long roleId);

    /**
     * 查询角色已授权的用户ID集合
     */
    List<Long> selectMemberUserIdsByRoleId(@Param("roleId") Long roleId);

    /**
     * 按角色维度批量插入用户-角色关联
     */
    int batchInsertUserRoleByRoleId(@Param("roleId") Long roleId, @Param("userIds") List<Long> userIds);

    /**
     * 解除单个用户与角色的关联
     */
    int deleteUserRoleByRoleIdAndUserId(@Param("roleId") Long roleId, @Param("userId") Long userId);

    /**
     * 删除角色的全部用户-角色关联
     */
    int deleteUserRoleByRoleId(@Param("roleId") Long roleId);
}
