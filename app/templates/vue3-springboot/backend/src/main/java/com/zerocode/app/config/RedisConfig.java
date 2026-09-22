package com.zerocode.app.config;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonTypeInfo;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.jsontype.BasicPolymorphicTypeValidator;
import com.fasterxml.jackson.databind.jsontype.PolymorphicTypeValidator;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.serializer.GenericJackson2JsonRedisSerializer;
import org.springframework.data.redis.serializer.StringRedisSerializer;

/**
 * Redis 配置（登录态缓存与后续通用缓存复用）
 */
@Configuration
public class RedisConfig {

    @Bean
    public RedisTemplate<String, Object> redisTemplate(RedisConnectionFactory connectionFactory) {
        RedisTemplate<String, Object> template = new RedisTemplate<>();
        template.setConnectionFactory(connectionFactory);

        StringRedisSerializer keySerializer = new StringRedisSerializer();
        GenericJackson2JsonRedisSerializer valueSerializer =
                new GenericJackson2JsonRedisSerializer(redisObjectMapper());

        template.setKeySerializer(keySerializer);
        template.setHashKeySerializer(keySerializer);
        template.setValueSerializer(valueSerializer);
        template.setHashValueSerializer(valueSerializer);
        template.afterPropertiesSet();
        return template;
    }

    /**
     * 缓存专用 ObjectMapper：
     * 必须注册 JavaTimeModule（否则 LocalDateTime 直接序列化失败），
     * 必须开启 default typing（否则反序列化退化为 LinkedHashMap，强转 LoginUser 在运行时报错）。
     * 与 Web 侧的全局 ObjectMapper 分开，Web 响应体不能带 @class 类型字段。
     */
    private ObjectMapper redisObjectMapper() {
        ObjectMapper mapper = new ObjectMapper();
        mapper.registerModule(new JavaTimeModule());
        mapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        mapper.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
        mapper.setSerializationInclusion(JsonInclude.Include.NON_NULL);
        mapper.activateDefaultTyping(subTypeValidator(),
                ObjectMapper.DefaultTyping.NON_FINAL,
                JsonTypeInfo.As.PROPERTY);
        return mapper;
    }

    /**
     * 不用 spec 初版写的 LaissezFaireSubTypeValidator：那是「任何 @class 都照单实例化」。
     * 本仓库 docker-compose 的 Redis 默认无密码（只绑 127.0.0.1），而 get 的读取目标是 Object，
     * 能连上 Redis 就能用 JdbcRowSetImpl 一类 gadget 打出 JNDI 出网，拿到 RCE。
     * 白名单按实测存储结构给出：LoginUser 本体 + roles/permissions 的 HashSet 包装 + 时间类型。
     */
    private PolymorphicTypeValidator subTypeValidator() {
        return BasicPolymorphicTypeValidator.builder()
                .allowIfSubType("com.zerocode.app.")
                .allowIfSubType("java.util.")
                .allowIfSubType("java.time.")
                .allowIfSubType("java.lang.")
                .build();
    }
}
