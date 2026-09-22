package com.zerocode.app.common;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

/**
 * Servlet工具类，获取当前请求信息
 */
public class ServletUtils {

    private ServletUtils() {
    }

    /**
     * 获取当前请求对象
     */
    public static HttpServletRequest getRequest() {
        ServletRequestAttributes attributes =
                (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        return attributes != null ? attributes.getRequest() : null;
    }

    /**
     * 获取客户端真实IP地址
     */
    public static String getClientIp() {
        HttpServletRequest request = getRequest();
        if (request == null) {
            return "unknown";
        }
        return IpUtils.getIpAddr(request);
    }
}
