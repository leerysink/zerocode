package com.zerocode.app.common;

import com.zerocode.app.security.LoginUser;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * 登录用户缓存（Redis）
 * AuthServiceImpl 写入/清除，JwtAuthenticationFilter 读取
 * 缓存丢失等价于登录态丢失，需重新登录
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class LoginUserCache {

    /**
     * 仅当会话仍存在时刷新快照，并保留剩余 TTL。
     * EXISTS 与 SET 在同一个 Lua 里执行，不会被其它的 DEL 插队：
     * 要么在踢线前刷新成功，要么看到 key 已消失而不写回，不存在「用旧快照重建会话」的窗口。
     * KEEPTTL 需 Redis 6.0+（本项目锁定 redis:7）。
     */
    private static final RedisScript<Long> REFRESH_IF_PRESENT = RedisScript.of(
            "if redis.call('EXISTS', KEYS[1]) == 1 then "
                    + "redis.call('SET', KEYS[1], ARGV[1], 'KEEPTTL') return 1 end return 0", Long.class);

    /** SCAN 每轮的候选数，也是批量 DEL 的分片大小 */
    private static final int BATCH_SIZE = 500;

    private final RedisTemplate<String, Object> redisTemplate;

    @Value("${jwt.expiration}")
    private long expirationMillis;

    /**
     * 仅用于登录成功时建立会话（全量 TTL）
     */
    public void put(Long userId, LoginUser loginUser) {
        redisTemplate.opsForValue().set(key(userId), loginUser, expirationMillis, TimeUnit.MILLISECONDS);
    }

    /**
     * 用于展示字段同步（改资料 / 拉取用户信息）：会话已失效时绝不重建
     */
    public void refresh(Long userId, LoginUser loginUser) {
        redisTemplate.execute(REFRESH_IF_PRESENT, List.of(key(userId)), loginUser);
    }

    public LoginUser get(Long userId) {
        String k = key(userId);
        try {
            Object value = redisTemplate.opsForValue().get(k);
            if (value == null) {
                return null;
            }
            if (value instanceof LoginUser loginUser) {
                return loginUser;
            }
            // 类型不符说明 key 被外部写过或结构已变更，不处理会让该用户每次请求都回到这里
            log.error("登录态类型不符，期望 LoginUser 实际 {}，已清除 key={}", value.getClass().getName(), k);
        } catch (Exception e) {
            // Redis 不可用与载荷解不出来均归入此分支：过滤器在 MVC 之前，异常会绕过全局异常处理器变成 500，
            // 这里按「未登录」吐回，让前端正常跳登录页
            log.error("读取登录态失败，按未登录处理 key={}", k, e);
        }
        deleteQuietly(k);
        return null;
    }

    private void deleteQuietly(String k) {
        try {
            redisTemplate.delete(k);
        } catch (Exception e) {
            log.warn("清除登录态 key 失败，交给 TTL 兜底 key={}", k, e);
        }
    }

    public void remove(Long userId) {
        redisTemplate.delete(key(userId));
    }

    public void removeByUserIds(Collection<Long> userIds) {
        if (userIds == null || userIds.isEmpty()) {
            return;
        }
        redisTemplate.delete(userIds.stream().map(LoginUserCache::key).toList());
    }

    /**
     * 权限/菜单变更后的全量失效，用 SCAN 避免 KEYS 阻塞
     */
    public void clearAll() {
        try (var cursor = redisTemplate.scan(ScanOptions.scanOptions()
                .match(Constants.REDIS_LOGIN_USER_PREFIX + "*").count(BATCH_SIZE).build())) {
            int n = 0;
            List<String> keys = new ArrayList<>(BATCH_SIZE);
            while (cursor.hasNext()) {
                keys.add(cursor.next());
                n++;
                if (keys.size() >= BATCH_SIZE) {
                    redisTemplate.delete(keys);
                    keys.clear();
                }
            }
            if (!keys.isEmpty()) {
                redisTemplate.delete(keys);
            }
            log.info("登录态缓存已全量清除，影响 {} 个会话", n);
        }
    }

    private static String key(Long userId) {
        return Constants.REDIS_LOGIN_USER_PREFIX + userId;
    }
}
