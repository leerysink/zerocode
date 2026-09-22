package com.zerocode.app.aspect;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 数据权限注解，标注在需要按部门数据范围收敛的查询方法上（约定标在 Service 实现类的方法上）
 * 切面会把当前登录用户的可见部门ID集合注入到所有实现 {@link DataScopeQuery} 的入参
 *
 * <p>必须标在<b>实现类</b>的方法上，标在接口方法上无效：{@code @annotation} 切点是
 * Spring AOP 在 CGLIB 代理的目标类方法上查找注解，而 Spring 默认不会因为接口上有注解
 * 而去代理接口方法。将来若把 {@code spring.aop.proxy-target-class} 改成 false（JDK 动态代理），
 * 切点会改为匹配接口方法、这里的注解就匹配不到了——届时切面会静默不生效而不是报错，
 * 改动该配置后必须重新验证数据权限是否仍然生效
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface DataScope {
}
