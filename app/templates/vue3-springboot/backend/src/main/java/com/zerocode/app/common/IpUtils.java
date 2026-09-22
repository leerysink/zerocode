package com.zerocode.app.common;

import jakarta.servlet.http.HttpServletRequest;

/**
 * IP地址工具类
 */
public class IpUtils {

    private IpUtils() {
    }

    /**
     * 获取客户端真实IP地址，支持多级代理
     */
    public static String getIpAddr(HttpServletRequest request) {
        if (request == null) {
            return "unknown";
        }
        String ip = request.getHeader("x-forwarded-for");
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getHeader("Proxy-Client-IP");
        }
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getHeader("X-Forwarded-For");
        }
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getHeader("WL-Proxy-Client-IP");
        }
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getHeader("X-Real-IP");
        }
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getRemoteAddr();
        }
        // 多个代理时取第一个
        if (ip != null && ip.contains(",")) {
            ip = ip.split(",")[0].trim();
        }
        return ip == null ? "unknown" : ip;
    }

    /**
     * 根据IP返回登录地点（简单实现，本地环境固定返回内网）
     */
    public static String getRealAddressByIP(String ip) {
        if (ip == null || ip.isEmpty()) {
            return "未知";
        }
        // 内网地址
        if (isInternalIp(ip)) {
            return "内网IP";
        }
        return "外网";
    }

    /**
     * 判断是否内网IP
     */
    public static boolean isInternalIp(String ip) {
        if ("unknown".equalsIgnoreCase(ip)) {
            return true;
        }
        if ("127.0.0.1".equals(ip) || "0:0:0:0:0:0:0:1".equals(ip) || "localhost".equalsIgnoreCase(ip)) {
            return true;
        }
        // 10.x.x.x / 172.16-31.x.x / 192.168.x.x
        if (ip.startsWith("10.") || ip.startsWith("192.168.")) {
            return true;
        }
        if (ip.startsWith("172.")) {
            String[] parts = ip.split("\\.");
            if (parts.length >= 2) {
                try {
                    int second = Integer.parseInt(parts[1]);
                    if (second >= 16 && second <= 31) {
                        return true;
                    }
                } catch (NumberFormatException ignored) {
                }
            }
        }
        return false;
    }
}
