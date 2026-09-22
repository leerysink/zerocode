package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.zerocode.app.common.BusinessException;
import com.zerocode.app.common.Constants;
import com.zerocode.app.common.LoginUserCache;
import com.zerocode.app.common.SecurityUtils;
import com.zerocode.app.dto.SysMenuDTO;
import com.zerocode.app.entity.SysMenu;
import com.zerocode.app.mapper.SysMenuMapper;
import com.zerocode.app.service.SysMenuService;
import com.zerocode.app.vo.TreeNodeVO;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 菜单服务实现
 */
@Service
@RequiredArgsConstructor
public class SysMenuServiceImpl implements SysMenuService {

    private final SysMenuMapper sysMenuMapper;
    private final LoginUserCache loginUserCache;

    @Override
    public List<SysMenu> list(String menuName) {
        LambdaQueryWrapper<SysMenu> wrapper = new LambdaQueryWrapper<>();
        wrapper.like(menuName != null && !menuName.isEmpty(), SysMenu::getMenuName, menuName);
        wrapper.orderByAsc(SysMenu::getParentId).orderByAsc(SysMenu::getSort);
        return sysMenuMapper.selectList(wrapper);
    }

    /**
     * 构建菜单树
     */
    @Override
    public List<TreeNodeVO> tree() {
        List<SysMenu> list = list(null);
        return buildTree(list);
    }

    private List<TreeNodeVO> buildTree(List<SysMenu> list) {
        List<TreeNodeVO> nodes = list.stream().map(item -> {
            TreeNodeVO node = new TreeNodeVO();
            node.setId(item.getId());
            node.setParentId(item.getParentId());
            node.setLabel(item.getMenuName());
            return node;
        }).collect(Collectors.toList());

        Map<Long, List<TreeNodeVO>> parentMap = nodes.stream()
                .collect(Collectors.groupingBy(n -> n.getParentId() == null ? 0L : n.getParentId()));

        List<TreeNodeVO> roots = parentMap.getOrDefault(0L, new ArrayList<>());
        for (TreeNodeVO node : nodes) {
            List<TreeNodeVO> children = parentMap.get(node.getId());
            if (children != null) {
                node.setChildren(children);
            }
        }
        return roots;
    }

    /**
     * 获取当前用户的路由菜单（自动补全角色缺失的父菜单）
     */
    @Override
    public List<SysMenu> getRouters() {
        Long userId = SecurityUtils.getUserId();
        // 超管返回全部菜单
        if (SecurityUtils.isAdmin()) {
            LambdaQueryWrapper<SysMenu> wrapper = new LambdaQueryWrapper<>();
            wrapper.in(SysMenu::getMenuType, Constants.MENU_TYPE_DIR, Constants.MENU_TYPE_MENU);
            wrapper.eq(SysMenu::getStatus, 1);
            wrapper.orderByAsc(SysMenu::getParentId).orderByAsc(SysMenu::getSort);
            return sysMenuMapper.selectList(wrapper);
        }
        // 非超管查询其角色关联的菜单
        List<SysMenu> menus = sysMenuMapper.selectMenusByUserId(userId);
        // 自动补全缺失的父菜单（角色只选了子菜单没选父目录时）
        Set<Long> existingIds = menus.stream().map(SysMenu::getId).collect(Collectors.toSet());
        Set<Long> parentIdsToFetch = new HashSet<>();
        for (SysMenu m : menus) {
            if (m.getParentId() != null && m.getParentId() != 0L && !existingIds.contains(m.getParentId())) {
                parentIdsToFetch.add(m.getParentId());
            }
        }
        if (!parentIdsToFetch.isEmpty()) {
            List<SysMenu> parentMenus = sysMenuMapper.selectList(
                    new LambdaQueryWrapper<SysMenu>()
                            .in(SysMenu::getId, parentIdsToFetch)
                            .eq(SysMenu::getStatus, 1));
            // 递归补全祖父菜单
            Set<Long> fetchedIds = new HashSet<>(existingIds);
            while (true) {
                Set<Long> missingParentIds = parentMenus.stream()
                        .filter(m -> m.getParentId() != null && m.getParentId() != 0L && !fetchedIds.contains(m.getParentId()))
                        .map(SysMenu::getParentId)
                        .collect(Collectors.toSet());
                if (missingParentIds.isEmpty()) break;
                List<SysMenu> grandParents = sysMenuMapper.selectList(
                        new LambdaQueryWrapper<SysMenu>()
                                .in(SysMenu::getId, missingParentIds)
                                .eq(SysMenu::getStatus, 1));
                parentMenus.addAll(grandParents);
                fetchedIds.addAll(missingParentIds);
            }
            // 合并原始菜单和补全的父菜单（去重）
            Set<Long> combinedIds = new HashSet<>(existingIds);
            for (SysMenu pm : parentMenus) {
                if (combinedIds.add(pm.getId())) {
                    menus.add(pm);
                }
            }
            menus.sort((a, b) -> {
                int cmp = a.getParentId().compareTo(b.getParentId());
                if (cmp == 0) cmp = a.getSort().compareTo(b.getSort());
                return cmp;
            });
            return menus;
        }
        return menus;
    }

    @Override
    public void add(SysMenuDTO dto) {
        SysMenu menu = new SysMenu();
        BeanUtils.copyProperties(dto, menu);
        if (menu.getParentId() == null) {
            menu.setParentId(0L);
        }
        if (menu.getSort() == null) {
            menu.setSort(0);
        }
        if (menu.getStatus() == null) {
            menu.setStatus(1);
        }
        if (menu.getVisible() == null) {
            menu.setVisible(1);
        }
        sysMenuMapper.insert(menu);
        // 新增菜单不写 sys_role_menu，没有任何用户的权限集会变，不能在此踢线（否则连建几个菜单就把全站登出几次）；
        // 真正需要失效的是把菜单绑定到角色的时候，那个钩子在 SysRoleServiceImpl.saveRoleRelations
    }

    @Override
    public void update(SysMenuDTO dto) {
        if (dto.getId() == null) {
            throw BusinessException.of("菜单ID不能为空");
        }
        if (dto.getId().equals(dto.getParentId())) {
            throw BusinessException.of("上级菜单不能选择自己");
        }
        SysMenu menu = new SysMenu();
        BeanUtils.copyProperties(dto, menu);
        sysMenuMapper.updateById(menu);
        loginUserCache.clearAll();
    }

    @Override
    public void delete(Long id) {
        LambdaQueryWrapper<SysMenu> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysMenu::getParentId, id);
        Long count = sysMenuMapper.selectCount(wrapper);
        if (count > 0) {
            throw BusinessException.of("存在子菜单，无法删除");
        }
        sysMenuMapper.deleteById(id);
        loginUserCache.clearAll();
    }
}
