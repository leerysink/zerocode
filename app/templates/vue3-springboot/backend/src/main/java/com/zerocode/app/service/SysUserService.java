package com.zerocode.app.service;

import com.zerocode.app.common.PageResult;
import com.zerocode.app.dto.SysUserDTO;
import com.zerocode.app.dto.SysUserQueryDTO;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.vo.SysUserVO;

import java.util.List;

/**
 * 用户服务
 */
public interface SysUserService {

    /**
     * 查询所有启用的用户列表（不含密码）
     * 实现上受 @DataScope 约束，只返回当前登录用户可见部门下的用户，
     * 与 {@link #page} 同一口径：不能出现下拉列表能看到、分页列表看不到的差异
     */
    List<SysUser> list(SysUserQueryDTO query);

    /**
     * 用户分页列表（左连接 sys_dept 返回部门名称；VO 无密码字段，无需再置空）
     * 实现上受 @DataScope 约束，只返回当前登录用户可见部门下的用户
     */
    PageResult<SysUserVO> page(Long current, Long size, SysUserQueryDTO query);

    /**
     * 用户详情，不属于当前登录用户可见部门时抛 403
     */
    SysUser getById(Long id);

    /**
     * 查询用户角色ID集合，目标用户不属于当前登录用户可见部门时抛 403
     */
    List<Long> getRoleIds(Long userId);

    /**
     * 新增用户：{@code dto.deptId} 非空时目标部门必须在当前登录用户可见范围内；
     * 不填部门表示暂不分配，不受数据权限约束
     */
    void add(SysUserDTO dto);

    /**
     * 修改用户：目标用户不在当前登录用户可见部门时抛 403；
     * {@code dto.deptId} 非空（即本次要改部门）时，目标部门也必须可见
     */
    void update(SysUserDTO dto);

    /**
     * 删除用户，目标用户不属于当前登录用户可见部门时抛 403
     */
    void delete(Long id);

    /**
     * 重置密码，目标用户不属于当前登录用户可见部门时抛 403
     */
    void resetPassword(Long id, String newPassword);
}
