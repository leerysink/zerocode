package com.zerocode.app;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.core.env.Environment;
import org.springframework.scheduling.annotation.EnableAsync;

@SpringBootApplication
@MapperScan("com.zerocode.app.mapper")
@EnableAsync
public class BaseApplication {

    public static void main(String[] args) {
        Environment env = SpringApplication.run(BaseApplication.class, args).getEnvironment();
        // 端口从环境读取（平台注入 SERVER_PORT），避免提示与实际端口不一致
        String baseUrl = "http://localhost:" + env.getProperty("server.port", "8080")
                + env.getProperty("server.servlet.context-path", "");
        System.out.println("""

                ===================================================
                  {{APP_NAME}} 启动成功!
                  后端地址: %s
                ===================================================
                """.formatted(baseUrl));
    }
}
