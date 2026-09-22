package com.zerocode.app.aspect;

import com.zerocode.app.common.Constants;
import com.zerocode.app.common.DataScopeResolver;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 数据权限切面
 * 拦截标注 {@link DataScope} 的方法，按当前登录用户的角色数据范围算出可见部门集合，
 * 注入到所有实现 {@link DataScopeQuery} 的入参上。
 *
 * <p>集合怎么算（含 admin / data_scope=1 的放行、无登录上下文的处理）全部在
 * {@link DataScopeResolver} 里，这里只负责「注入还是放行」，不再自己查库判断：
 * 切面与服务层单条校验必须用同一套规则，否则会出现列表看不到、详情却能打开的差集。
 */
@Slf4j
@Aspect
@Component
@RequiredArgsConstructor
public class DataScopeAspect {

    private final DataScopeResolver dataScopeResolver;

    @Around("@annotation(dataScope)")
    public Object around(ProceedingJoinPoint joinPoint, DataScope dataScope) throws Throwable {
        List<Long> visibleDeptIds = dataScopeResolver.visibleDeptIds();

        if (visibleDeptIds == null) {
            // 不受限（超管 / data_scope=1）
            releaseDataScope(joinPoint.getArgs());
            return joinPoint.proceed();
        }

        // 空集合（未分配部门/角色全被停用/自定义部门为空）时用哨兵占位，
        // 保证 XML 的 IN 语法合法且匹配不到任何数据 —— 宁可查不到，不能漏权限
        if (visibleDeptIds.isEmpty()) {
            visibleDeptIds = List.of(Constants.EMPTY_DATA_SCOPE_SENTINEL);
        }

        for (Object arg : joinPoint.getArgs()) {
            if (arg instanceof DataScopeQuery query) {
                query.setVisibleDeptIds(visibleDeptIds);
                query.setDataScopeFiltered(true);
            }
        }

        return joinPoint.proceed();
    }

    /**
     * 放行分支同样要覆写数据权限字段。
     * visibleDeptIds / dataScopeFiltered 是切面的内部约定，但查询 DTO 走 Spring MVC
     * 参数绑定时客户端也能塞值（如 ?dataScopeFiltered=true&visibleDeptIds=3）；
     * 不在放行分支清空，前端就能自带过滤条件把管理员看到的数据缩小。
     */
    private void releaseDataScope(Object[] args) {
        for (Object arg : args) {
            if (arg instanceof DataScopeQuery query) {
                query.setVisibleDeptIds(null);
                query.setDataScopeFiltered(false);
            }
        }
    }
}
