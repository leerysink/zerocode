package com.zerocode.app.service;

import com.zerocode.app.dto.SysDeptDTO;
import com.zerocode.app.entity.SysDept;
import com.zerocode.app.vo.TreeNodeVO;

import java.util.List;

/**
 * 部门服务
 */
public interface SysDeptService {

    List<SysDept> list(String deptName, Integer status);

    /**
     * 构建部门树（含停用部门，只排除逻辑删除的部门；管理端需要看到停用项才能重新启用它）
     */
    List<TreeNodeVO> tree();

    SysDept getById(Long id);

    void add(SysDeptDTO dto);

    void update(SysDeptDTO dto);

    void delete(Long id);

    /**
     * 查询部门及其所有子孙部门ID（含自身），供移动部门时的环校验使用。
     * 数据权限不走这里：可见部门集合由 SysUserMapper#selectVisibleDeptIds
     * 一条递归 CTE 直接算出，不依赖 ancestors 也不需逐个部门展开
     */
    List<Long> selectDescendantIds(Long deptId);
}
