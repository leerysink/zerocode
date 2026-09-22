package com.zerocode.app.aspect;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.zerocode.app.annotation.BusinessType;
import com.zerocode.app.annotation.Log;
import com.zerocode.app.common.IpUtils;
import com.zerocode.app.common.SecurityUtils;
import com.zerocode.app.common.ServletUtils;
import com.zerocode.app.entity.SysOperLog;
import com.zerocode.app.security.LoginUser;
import com.zerocode.app.service.SysOperLogService;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.JoinPoint;
import org.aspectj.lang.annotation.AfterReturning;
import org.aspectj.lang.annotation.AfterThrowing;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import org.springframework.web.multipart.MultipartFile;

import java.lang.reflect.Method;
import java.lang.reflect.Parameter;
import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 操作日志AOP切面
 * 拦截标注 @Log 注解的Controller方法，自动记录操作日志
 */
@Slf4j
@Aspect
@Component
@RequiredArgsConstructor
public class OperLogAspect {

    private final SysOperLogService sysOperLogService;
    private final ObjectMapper objectMapper;

    private static final ThreadLocal<Long> START_TIME = new ThreadLocal<>();

    /** 参数名命中即脱敏：不得把明文口令写进 sys_oper_log.oper_param，该表可由日志分页接口读出 */
    private static final Pattern SENSITIVE_PARAM = Pattern.compile("password|passwd|secret|token", Pattern.CASE_INSENSITIVE);
    private static final String MASKED = "******";

    /**
     * 方法执行前记录开始时间
     */
    @Before("@annotation(controllerLog)")
    public void doBefore(Log controllerLog) {
        START_TIME.set(System.currentTimeMillis());
    }

    /**
     * 方法正常返回后记录日志
     */
    @AfterReturning(pointcut = "@annotation(controllerLog)", returning = "result")
    public void doAfterReturning(JoinPoint joinPoint, Log controllerLog, Object result) {
        handleLog(joinPoint, controllerLog, null, result);
    }

    /**
     * 方法抛出异常后记录日志
     */
    @AfterThrowing(pointcut = "@annotation(controllerLog)", throwing = "e")
    public void doAfterThrowing(JoinPoint joinPoint, Log controllerLog, Exception e) {
        handleLog(joinPoint, controllerLog, e, null);
    }

    private void handleLog(JoinPoint joinPoint, Log controllerLog, Exception e, Object result) {
        try {
            SysOperLog operLog = new SysOperLog();
            // 操作状态：异常为0，正常为1
            operLog.setStatus(e == null ? 1 : 0);

            // 请求信息
            HttpServletRequest request = ServletUtils.getRequest();
            if (request != null) {
                String ip = IpUtils.getIpAddr(request);
                operLog.setOperIp(ip);
                operLog.setOperLocation(IpUtils.getRealAddressByIP(ip));
                operLog.setRequestMethod(request.getMethod());
                operLog.setOperUrl(request.getRequestURI());
            }

            // 操作人信息：名称存昵称快照，昵称为空时回退登录名
            LoginUser loginUser = SecurityUtils.getLoginUser();
            if (loginUser != null) {
                operLog.setOperUserId(loginUser.getUserId());
                operLog.setOperUserName(resolveOperUserName(loginUser));
            }

            // 注解信息
            operLog.setTitle(controllerLog.title());
            BusinessType businessType = controllerLog.businessType();
            operLog.setBusinessType(businessType.getCode());

            // 方法名
            MethodSignature signature = (MethodSignature) joinPoint.getSignature();
            Method method = signature.getMethod();
            operLog.setMethod(joinPoint.getTarget().getClass().getName() + "." + method.getName() + "()");

            // 请求参数
            operLog.setOperParam(getRequestParams(joinPoint, method));

            // 返回结果（正常时记录，限制长度）
            if (e == null && result != null) {
                String jsonResult = objectMapper.writeValueAsString(result);
                if (jsonResult.length() > 2000) {
                    jsonResult = jsonResult.substring(0, 2000) + "...";
                }
                operLog.setJsonResult(jsonResult);
            }

            // 错误消息
            if (e != null) {
                String errorMsg = e.getMessage();
                if (errorMsg != null && errorMsg.length() > 2000) {
                    errorMsg = errorMsg.substring(0, 2000);
                }
                operLog.setErrorMsg(errorMsg);
            }

            // 消耗时间
            Long startTime = START_TIME.get();
            if (startTime != null) {
                operLog.setCostTime(System.currentTimeMillis() - startTime);
            }
            operLog.setCreateTime(LocalDateTime.now());

            // 异步保存
            sysOperLogService.asyncSave(operLog);
        } catch (Exception ex) {
            log.error("记录操作日志失败: {}", ex.getMessage());
        } finally {
            START_TIME.remove();
        }
    }

    /**
     * 操作人展示名：优先昵称，昵称为空时回退登录名
     */
    private String resolveOperUserName(LoginUser loginUser) {
        return StringUtils.hasText(loginUser.getNickname()) ? loginUser.getNickname() : loginUser.getUsername();
    }

    /**
     * 获取请求参数
     */
    private String getRequestParams(JoinPoint joinPoint, Method method) {
        try {
            Object[] args = joinPoint.getArgs();
            Parameter[] parameters = method.getParameters();
            Map<String, Object> paramMap = new HashMap<>();
            for (int i = 0; i < parameters.length; i++) {
                if (args[i] == null) {
                    continue;
                }
                // 跳过不可序列化的参数
                if (args[i] instanceof HttpServletRequest
                        || args[i] instanceof MultipartFile
                        || args[i] instanceof org.springframework.web.multipart.MultipartHttpServletRequest) {
                    continue;
                }
                String paramName = parameters[i].getName();
                paramMap.put(paramName, SENSITIVE_PARAM.matcher(paramName).find() ? MASKED : args[i]);
            }
            if (paramMap.isEmpty()) {
                return null;
            }
            String json = objectMapper.writeValueAsString(paramMap);
            if (json.length() > 2000) {
                json = json.substring(0, 2000) + "...";
            }
            return json;
        } catch (Exception e) {
            log.warn("获取请求参数失败: {}", e.getMessage());
            return null;
        }
    }
}
