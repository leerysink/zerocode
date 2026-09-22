package com.zerocode.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.zerocode.app.entity.SysMenu;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 菜单Mapper
 */
@Mapper
public interface SysMenuMapper extends BaseMapper<SysMenu> {

    /**
     * 根据用户ID查询菜单（用于构建路由）
     */
    List<SysMenu> selectMenusByUserId(@Param("userId") Long userId);

    /**
     * 根据角色ID查询菜单ID
     */
    List<Long> selectMenuIdsByRoleId(@Param("roleId") Long roleId);
}
