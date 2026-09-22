package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.zerocode.app.common.BusinessException;
import com.zerocode.app.dto.SysDeptDTO;
import com.zerocode.app.entity.SysDept;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.mapper.SysDeptMapper;
import com.zerocode.app.mapper.SysUserMapper;
import com.zerocode.app.service.SysDeptService;
import com.zerocode.app.vo.TreeNodeVO;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 部门服务实现
 */
@Service
@RequiredArgsConstructor
public class SysDeptServiceImpl implements SysDeptService {

    private final SysDeptMapper sysDeptMapper;
    private final SysUserMapper sysUserMapper;

    @Override
    public List<SysDept> list(String deptName, Integer status) {
        LambdaQueryWrapper<SysDept> wrapper = new LambdaQueryWrapper<>();
        wrapper.like(deptName != null && !deptName.isEmpty(), SysDept::getDeptName, deptName);
        wrapper.eq(status != null, SysDept::getStatus, status);
        wrapper.orderByAsc(SysDept::getParentId).orderByAsc(SysDept::getOrderNum).orderByAsc(SysDept::getId);
        return sysDeptMapper.selectList(wrapper);
    }

    @Override
    public List<TreeNodeVO> tree() {
        List<SysDept> list = list(null, null);
        List<TreeNodeVO> nodes = list.stream().map(item -> {
            TreeNodeVO node = new TreeNodeVO();
            node.setId(item.getId());
            node.setParentId(item.getParentId());
            node.setLabel(item.getDeptName());
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

    @Override
    public SysDept getById(Long id) {
        SysDept dept = sysDeptMapper.selectById(id);
        if (dept == null) {
            throw BusinessException.of("部门不存在");
        }
        return dept;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void add(SysDeptDTO dto) {
        SysDept dept = new SysDept();
        BeanUtils.copyProperties(dto, dept);
        dept.setId(null);
        dept.setAncestors(buildAncestors(dto.getParentId()));
        if (dept.getOrderNum() == null) {
            dept.setOrderNum(0);
        }
        if (dept.getStatus() == null) {
            dept.setStatus(1);
        }
        sysDeptMapper.insert(dept);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void update(SysDeptDTO dto) {
        if (dto.getId() == null) {
            throw BusinessException.of("部门ID不能为空");
        }
        if (dto.getParentId() != null && dto.getParentId().equals(dto.getId())) {
            throw BusinessException.of("上级部门不能是当前部门");
        }
        if (dto.getParentId() != null && dto.getParentId() != 0L) {
            List<Long> subtreeIds = selectDescendantIds(dto.getId());
            if (subtreeIds.contains(dto.getParentId())) {
                throw BusinessException.of("上级部门不能是当前部门或其子部门");
            }
        }
        SysDept before = getById(dto.getId());
        SysDept dept = new SysDept();
        BeanUtils.copyProperties(dto, dept);
        // parentId 缺省即局部更新：MyBatis-Plus 默认 NOT_NULL 策略下 parent_id 不会被写，
        // 所以 ancestors 也必须保持原值，否则一次改名或停用就会把部门悄悄挂成顶级
        String ancestors = dto.getParentId() == null ? null : buildAncestors(dto.getParentId());
        dept.setAncestors(ancestors);
        sysDeptMapper.updateById(dept);
        // 祖级路径真的变了才下推整棵子树，否则后代的 ancestors 会永久停留在移动前
        if (ancestors != null && !Objects.equals(before.getAncestors(), ancestors)) {
            cascadeAncestors(dto.getId(), ancestors);
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void delete(Long id) {
        if (sysDeptMapper.countChildren(id) > 0) {
            throw BusinessException.of("存在子部门，无法删除");
        }
        Long userCount = sysUserMapper.selectCount(
                new LambdaQueryWrapper<SysUser>().eq(SysUser::getDeptId, id));
        if (userCount != null && userCount > 0) {
            throw BusinessException.of("部门下存在用户，无法删除");
        }
        sysDeptMapper.deleteById(id);
    }

    @Override
    public List<Long> selectDescendantIds(Long deptId) {
        if (deptId == null) {
            return List.of();
        }
        return sysDeptMapper.selectDescendantIds(deptId);
    }

    /**
     * 顶级部门 ancestors 为 0，其余为「父.ancestors + 父.id + ,」
     */
    private String buildAncestors(Long parentId) {
        if (parentId == null || parentId == 0L) {
            return "0";
        }
        SysDept parent = getById(parentId);
        return appendAncestor(parent.getAncestors(), parent.getId());
    }

    /**
     * 在祖级路径尾部追加一个节点 id。
     * 路径已经以逗号结尾时不再补分隔符，否则三级部门会拼出 `0,1,,2,` 这样的空段
     */
    private String appendAncestor(String ancestors, Long id) {
        if (ancestors == null || ancestors.isEmpty()) {
            return id + ",";
        }
        String separator = ancestors.endsWith(",") ? "" : ",";
        return ancestors + separator + id + ",";
    }

    /**
     * 把 ancestors 变更下推到 rootId 的全部后代。
     * 一次全量查询后在内存里按 parent_id 分组，避免为级联更新再加一条递归 SQL；
     * visited 用来兜住脏数据成环（正常 API 走不出环，见 update 的防环校验）
     */
    private void cascadeAncestors(Long rootId, String rootAncestors) {
        Map<Long, List<SysDept>> childrenMap = list(null, null).stream()
                .filter(item -> item.getParentId() != null)
                .collect(Collectors.groupingBy(SysDept::getParentId));
        pushAncestors(childrenMap, rootId, rootAncestors, new HashSet<>());
    }

    private void pushAncestors(Map<Long, List<SysDept>> childrenMap, Long parentId,
                               String parentAncestors, Set<Long> visited) {
        if (!visited.add(parentId)) {
            return;
        }
        String childAncestors = appendAncestor(parentAncestors, parentId);
        for (SysDept child : childrenMap.getOrDefault(parentId, List.of())) {
            // 只带 id 与 ancestors，MyBatis-Plus 默认 NOT_NULL 策略保证不会把其它列写空
            SysDept patch = new SysDept();
            patch.setId(child.getId());
            patch.setAncestors(childAncestors);
            sysDeptMapper.updateById(patch);
            pushAncestors(childrenMap, child.getId(), childAncestors, visited);
        }
    }
}
