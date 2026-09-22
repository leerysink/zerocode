package com.zerocode.app.common;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 用户代理(User-Agent)解析工具类
 * 简单解析浏览器类型和操作系统
 */
public class UserAgentUtils {

    private UserAgentUtils() {
    }

    /**
     * 获取浏览器类型
     */
    public static String getBrowser(HttpServletRequest request) {
        if (request == null) {
            return "unknown";
        }
        String ua = request.getHeader("User-Agent");
        if (ua == null || ua.isEmpty()) {
            return "unknown";
        }
        if (ua.contains("Edg")) {
            return "Edge";
        } else if (ua.contains("Chrome")) {
            return "Chrome";
        } else if (ua.contains("Firefox")) {
            return "Firefox";
        } else if (ua.contains("Safari")) {
            return "Safari";
        } else if (ua.contains("MSIE") || ua.contains("Trident")) {
            return "IE";
        } else if (ua.contains("Opera") || ua.contains("OPR")) {
            return "Opera";
        }
        return "unknown";
    }

    /**
     * 获取操作系统
     */
    public static String getOs(HttpServletRequest request) {
        if (request == null) {
            return "unknown";
        }
        String ua = request.getHeader("User-Agent");
        if (ua == null || ua.isEmpty()) {
            return "unknown";
        }
        if (ua.contains("Windows")) {
            if (ua.contains("Windows NT 10")) {
                return "Windows 10/11";
            } else if (ua.contains("Windows NT 6.3")) {
                return "Windows 8.1";
            } else if (ua.contains("Windows NT 6.2")) {
                return "Windows 8";
            } else if (ua.contains("Windows NT 6.1")) {
                return "Windows 7";
            }
            return "Windows";
        } else if (ua.contains("Mac OS X") || ua.contains("Macintosh")) {
            return "macOS";
        } else if (ua.contains("Android")) {
            return "Android";
        } else if (ua.contains("iPhone") || ua.contains("iPad")) {
            return "iOS";
        } else if (ua.contains("Linux")) {
            return "Linux";
        }
        return "unknown";
    }
}
