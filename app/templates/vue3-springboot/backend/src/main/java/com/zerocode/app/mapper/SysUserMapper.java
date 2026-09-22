package com.zerocode.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.zerocode.app.dto.SysUserQueryDTO;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.vo.SysUserVO;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 用户Mapper
 */
@Mapper
public interface SysUserMapper extends BaseMapper<SysUser> {

    /**
     * 根据用户名查询用户
     */
    SysUser selectByUsername(@Param("username") String username);

    /**
     * 用户分页列表（左连接 sys_dept 取部门名称，不过滤 sys_dept 的逻辑删除，
     * 保证指向历史/已删部门的用户也能显示名称）
     */
    IPage<SysUserVO> selectUserPage(IPage<SysUserVO> page, @Param("query") SysUserQueryDTO query);

    /**
     * 查询用户角色key集合
     */
    List<String> selectRoleKeysByUserId(@Param("userId") Long userId);

    /**
     * 查询用户权限标识集合
     */
    List<String> selectPermissionsByUserId(@Param("userId") Long userId);

    /**
     * 查询用户可见部门ID集合（基于角色数据范围：2=本部门 3=本部门及以下 4=自定义）
     */
    List<Long> selectVisibleDeptIds(@Param("userId") Long userId);

    /**
     * 判断用户是否拥有 data_scope=1(全部可见) 的角色
     */
    Integer hasAllDataScope(@Param("userId") Long userId);
}
