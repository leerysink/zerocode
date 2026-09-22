package com.zerocode.app.common;

import com.zerocode.app.mapper.SysUserMapper;
import com.zerocode.app.security.LoginUser;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 部门数据范围解析器。
 *
 * <p>「当前登录用户能看到哪些部门」这件事在本项目里只有这一个算法实现：
 * {@link com.zerocode.app.aspect.DataScopeAspect} 用它给分页/列表注入集合，
 * 服务层用它做单条记录的归属校验。两处分开算迟早会漂移（一边加了条件另一边没加），
 * 所以不要绕过这里直接调 SysUserMapper。
 */
@Component
@RequiredArgsConstructor
public class DataScopeResolver {

    private final SysUserMapper sysUserMapper;

    /**
     * 当前登录用户的可见部门ID集合。
     *
     * @return {@code null} 表示不受限（超管角色，或拥有 data_scope=1 的角色）；
     *         非 null 时可能是<b>空集合</b>，含义是「什么都看不到」，调用方必须按看不到处理
     * @throws BusinessException 无登录上下文。定时任务、{@code @Async} 线程里 SecurityContext
     *         不会跨线程传递，此时若放行就等于把全量数据交出去（fail-open），
     *         所以直接抛：这类后台任务应该走不带数据权限的专用查询，而不是复用页面方法
     */
    public List<Long> visibleDeptIds() {
        LoginUser loginUser = SecurityUtils.getLoginUser();
        if (loginUser == null) {
            throw BusinessException.of("当前线程没有登录上下文，不能执行需要部门数据权限的查询"
                    + "（定时任务/@Async 请在 Service 之外另开不受数据权限约束的方法）");
        }

        // 超管与 data_scope=1 都在集合计算之前放行，省掉一次递归 CTE
        if (loginUser.getRoles() != null && loginUser.getRoles().contains(Constants.ROLE_ADMIN)) {
            return null;
        }
        Integer allScopeCount = sysUserMapper.hasAllDataScope(loginUser.getUserId());
        if (allScopeCount != null && allScopeCount > 0) {
            return null;
        }

        List<Long> visible = sysUserMapper.selectVisibleDeptIds(loginUser.getUserId());
        return visible == null ? List.of() : visible;
    }

    /**
     * 校验某个部门落在当前登录用户的可见范围内，用于单条记录的读写。
     *
     * @param deptId 目标记录所属部门；null（未分配部门）对受限用户一律不可见，
     *               与列表 SQL 的 {@code dept_id IN (...)} 行为一致，不存在「列表里看不到但能按 id 打开」的差集
     * @throws BusinessException 403 不在可见范围
     */
    public void requireVisibleDept(Long deptId) {
        List<Long> visible = visibleDeptIds();
        if (visible == null) {
            return;
        }
        if (deptId == null || !visible.contains(deptId)) {
            throw BusinessException.of(403, "该数据不在你的部门数据范围内");
        }
    }
}
