package com.zerocode.app.annotation;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 自定义操作日志注解
 * 标注在Controller方法上，由 OperLogAspect 切面记录操作日志
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface Log {

    /**
     * 模块标题（如：用户管理）
     */
    String title() default "";

    /**
     * 业务操作类型
     */
    BusinessType businessType() default BusinessType.OTHER;
}
