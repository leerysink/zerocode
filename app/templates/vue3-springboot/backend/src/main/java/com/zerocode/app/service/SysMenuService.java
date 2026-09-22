package com.zerocode.app.service;

import com.zerocode.app.dto.SysMenuDTO;
import com.zerocode.app.entity.SysMenu;
import com.zerocode.app.vo.TreeNodeVO;

import java.util.List;

/**
 * 菜单服务
 */
public interface SysMenuService {

    List<SysMenu> list(String menuName);

    /**
     * 构建菜单树
     */
    List<TreeNodeVO> tree();

    /**
     * 获取当前用户的路由菜单（自动补全角色缺失的父菜单）
     */
    List<SysMenu> getRouters();

    void add(SysMenuDTO dto);

    void update(SysMenuDTO dto);

    void delete(Long id);
}
