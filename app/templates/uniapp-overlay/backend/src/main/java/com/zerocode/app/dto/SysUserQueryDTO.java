package com.zerocode.app.dto;

import com.zerocode.app.aspect.DataScopeQuery;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

import java.util.List;

/**
 * 用户列表查询DTO
 *
 * <p>本类是 {@code GET} 接口直接拿 POJO 接参的写法（Spring MVC 按 query 参数逐个绑定），
 * 所以字段名就是对外参数名：下面两个带 {@code @Schema(hidden)} 的内部字段虽然不会出现在
 * 接口文档里，但绑定层依旧接受它们。安全性不靠文档隐藏，靠的是
 * {@link com.zerocode.app.aspect.DataScopeAspect} 在每条分支（含放行分支）都显式覆写这两个字段
 */
@Data
public class SysUserQueryDTO implements DataScopeQuery {

    /** 用户名模糊匹配，null/空串表示不过滤 */
    private String username;

    /** 状态筛选: 1=正常 0=停用，null=全部 */
    private Integer status;

    /** 数据权限: 可见部门ID集合（由 @DataScope 切面注入，勿手工赋值） */
    @Schema(hidden = true)
    private List<Long> visibleDeptIds;

    /** 数据权限: 是否需要拼接可见部门条件（由切面置 true） */
    @Schema(hidden = true)
    private boolean dataScopeFiltered = false;

    /**
     * 用户范围: 空 = 后台用户（排除「移动端用户」角色）；传角色 key 时只看挂该角色的用户。
     * 移动端注册出来的 C 端账号默认不进后台浏览面，显式筛选该角色时才出现
     */
    private String roleKey;
}
