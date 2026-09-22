package com.zerocode.app.aspect;

import java.util.List;

/**
 * 数据权限查询参数契约。
 * 需要按部门数据范围收敛的查询 DTO 实现此接口，
 * {@link DataScopeAspect} 会把当前登录用户的可见部门ID集合注入进来，
 * Mapper XML 再据此拼出 IN 条件。
 *
 * <p>实现类的 visibleDeptIds / dataScopeFiltered 属于切面内部字段：
 * 它们通常会被 Spring MVC 从 query 参数绑定（存在客户端伪造的可能），
 * 因此切面在每条分支上都会显式覆写这两个字段，业务代码不要手工赋值。
 *
 * <p>getter 与 setter 同样必需：MyBatis-Plus 的 {@code QueryWrapper} 分支（如用户下拉列表）
 * 要把切面刚注入的值读回来。这里显式声明而不只留 setter，是为了让实现类漏定义
 * 某个 getter 时在编译期就报错，而不是等到运行期 OGNL 取不到属性、把条件当成 false 静默跳过。
 */
public interface DataScopeQuery {

    /**
     * 注入可见部门ID集合；未做数据权限过滤时为 null
     */
    void setVisibleDeptIds(List<Long> visibleDeptIds);

    /**
     * 标记本次查询是否需要拼接数据权限条件
     */
    void setDataScopeFiltered(boolean dataScopeFiltered);

    /**
     * 读取切面注入的可见部门ID集合，未过滤时为 null
     */
    List<Long> getVisibleDeptIds();

    /**
     * 读取本次查询是否需要拼接数据权限条件
     */
    boolean isDataScopeFiltered();
}
