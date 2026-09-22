package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.zerocode.app.aspect.DataScope;
import com.zerocode.app.common.BusinessException;
import com.zerocode.app.common.DataScopeResolver;
import com.zerocode.app.common.LoginUserCache;
import com.zerocode.app.common.PageResult;
import com.zerocode.app.dto.SysUserDTO;
import com.zerocode.app.dto.SysUserQueryDTO;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.mapper.SysRoleMapper;
import com.zerocode.app.mapper.SysUserMapper;
import com.zerocode.app.service.SysUserService;
import com.zerocode.app.vo.SysUserVO;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.List;

/**
 * 用户服务实现
 */
@Service
@RequiredArgsConstructor
public class SysUserServiceImpl implements SysUserService {

    private final SysUserMapper sysUserMapper;
    private final SysRoleMapper sysRoleMapper;
    private final PasswordEncoder passwordEncoder;
    private final LoginUserCache loginUserCache;
    private final DataScopeResolver dataScopeResolver;

    /**
     * 查询启用状态的用户列表（不含密码）
     * 这里用 QueryWrapper 而不是手写 XML，所以数据权限靠切面注入后读回来自己拼 IN，
     * 而不是像 {@link #page} 那样交给 XML 的 {@code <if test="query.dataScopeFiltered">}
     */
    @Override
    @DataScope
    public List<SysUser> list(SysUserQueryDTO query) {
        LambdaQueryWrapper<SysUser> wrapper = new LambdaQueryWrapper<SysUser>()
                .eq(SysUser::getStatus, 1)
                .eq(SysUser::getDeleted, 0)
                .orderByAsc(SysUser::getId);
        // 只看切面置的 filtered 标志，不信 visibleDeptIds 本身是否为 null：
        // 客户端可以单独传 visibleDeptIds 而不传 filtered，此时不应参与拼条件
        if (query.isDataScopeFiltered()) {
            wrapper.in(SysUser::getDeptId, query.getVisibleDeptIds());
        }
        List<SysUser> list = sysUserMapper.selectList(wrapper);
        list.forEach(u -> u.setPassword(null));
        return list;
    }

    /**
     * 用户分页列表（左连接 sys_dept 返回部门名称；VO 无密码字段，无需再置空）
     *
     * <p>@DataScope 标在实现类而不是控制器上，是为了让所有进 service 的调用都走同一入口。
     * 但要注意它的失效方式：<b>忘标注解是静默不过滤（fail-open），不是报错</b>——
     * {@code dataScopeFiltered} 默认 false，Mapper XML 里的 {@code <if>} 直接不拼条件。
     * 所以新增需要收敛的查询方法时，必须同时加 @DataScope + 在 SQL 里拼条件 + 补一条越权用例；
     * 真正的兜底防线是 {@link DataScopeResolver#requireVisibleDept}（单条读写校验，默认拒绝）
     */
    @Override
    @DataScope
    public PageResult<SysUserVO> page(Long current, Long size, SysUserQueryDTO query) {
        Page<SysUserVO> page = new Page<>(current, size);
        return PageResult.of(sysUserMapper.selectUserPage(page, query));
    }

    @Override
    public SysUser getById(Long id) {
        SysUser user = sysUserMapper.selectById(id);
        if (user == null) {
            throw BusinessException.of("用户不存在");
        }
        // 详情接口不拼列表 SQL，只能按主键取，所以这里必须单独校归属，
        // 否则猜到 id 就能读到别人部门的用户资料
        dataScopeResolver.requireVisibleDept(user.getDeptId());
        user.setPassword(null);
        return user;
    }

    /**
     * 查询用户角色ID集合
     */
    @Override
    public List<Long> getRoleIds(Long userId) {
        // 先取目标用户拿它的部门再校归属；查不到用户（已删除/id 不存在）时不报错，
        // 保持与改造前一致地返回空集合，反正无数据可泄
        SysUser target = sysUserMapper.selectById(userId);
        if (target != null) {
            dataScopeResolver.requireVisibleDept(target.getDeptId());
        }
        return sysRoleMapper.selectRolesByUserId(userId).stream()
                .map(r -> r.getId())
                .toList();
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void add(SysUserDTO dto) {
        // 校目标部门，但允许不填部门：deptId 为 null 是「先建人后补部门」的常见录入顺序，放行；
        // 但部门管理员不能把新人直接塞进自己看不到的部门，那等于绕过 update 的目标侧校验往别人地盘埋数据；
        // 不分配部门的用户对所有受限用户都不可见（可见集合要求 dept_id IS NOT NULL），本身已是 fail-closed
        if (dto.getDeptId() != null) {
            dataScopeResolver.requireVisibleDept(dto.getDeptId());
        }
        SysUser exist = sysUserMapper.selectByUsername(dto.getUsername());
        if (exist != null) {
            throw BusinessException.of("用户名已存在");
        }
        SysUser user = new SysUser();
        BeanUtils.copyProperties(dto, user);
        if (StringUtils.hasText(dto.getPassword())) {
            user.setPassword(passwordEncoder.encode(dto.getPassword()));
        } else {
            user.setPassword(passwordEncoder.encode("123456"));
        }
        if (user.getStatus() == null) {
            user.setStatus(1);
        }
        sysUserMapper.insert(user);
        // 分配角色
        if (dto.getRoleIds() != null && !dto.getRoleIds().isEmpty()) {
            sysRoleMapper.batchInsertUserRole(user.getId(), dto.getRoleIds());
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void update(SysUserDTO dto) {
        if (dto.getId() == null) {
            throw BusinessException.of("用户ID不能为空");
        }
        // 双侧归属校验：当前部门决定「你能不能改这个人」，目标部门决定「你能不能把他移到这里」；
        // 只校一侧会漏：校不到目标就能把人移出可见范围，校不到当前就能隔着部门改人
        SysUser current = sysUserMapper.selectById(dto.getId());
        if (current == null) {
            throw BusinessException.of("用户不存在");
        }
        dataScopeResolver.requireVisibleDept(current.getDeptId());
        // dto.deptId 为 null 是「不改部门」（MP 默认 NOT_NULL 策略不更新 null 字段），
        // 不能当成「把用户移成无部门」来拦，否则前端不带 deptId 的保存会被误拒
        if (dto.getDeptId() != null) {
            dataScopeResolver.requireVisibleDept(dto.getDeptId());
        }
        SysUser user = new SysUser();
        BeanUtils.copyProperties(dto, user);
        user.setUsername(null);
        if (StringUtils.hasText(dto.getPassword())) {
            user.setPassword(passwordEncoder.encode(dto.getPassword()));
        } else {
            user.setPassword(null);
        }
        sysUserMapper.updateById(user);
        // 重新分配角色
        sysRoleMapper.deleteUserRoleByUserId(dto.getId());
        if (dto.getRoleIds() != null && !dto.getRoleIds().isEmpty()) {
            sysRoleMapper.batchInsertUserRole(dto.getId(), dto.getRoleIds());
        }
        // 改角色/改密码/改状态都会使已缓存的权限快照过期，踢线重新登录
        loginUserCache.remove(dto.getId());
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void delete(Long id) {
        requireOwnedUser(id);
        sysRoleMapper.deleteUserRoleByUserId(id);
        sysUserMapper.deleteById(id);
        // 不踢线则被删用户凭 Redis 里的旧快照与未过期 token 仍可继续访问
        loginUserCache.remove(id);
    }

    /**
     * 重置密码
     */
    @Override
    public void resetPassword(Long id, String newPassword) {
        requireOwnedUser(id);
        SysUser user = new SysUser();
        user.setId(id);
        user.setPassword(passwordEncoder.encode(newPassword));
        sysUserMapper.updateById(user);
        // 与自己改密同口径：改完强制下线
        loginUserCache.remove(id);
    }

    /**
     * 写侧归属校验的共用入口：取目标用户并校它的部门。
     *
     * <p>查不到就直接拒，不能「拿不到部门就放过」：那正是本次要堵的 fail-open；
     * 超管在 {@code requireVisibleDept} 里会拿到 null 可见集合而无条件通过，不受影响
     */
    private void requireOwnedUser(Long id) {
        SysUser target = sysUserMapper.selectById(id);
        if (target == null) {
            throw BusinessException.of("用户不存在");
        }
        dataScopeResolver.requireVisibleDept(target.getDeptId());
    }
}
