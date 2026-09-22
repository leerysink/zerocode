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
        // 端口按当前环境配置读取（dev/prod 各自不同），避免提示与实际端口不一致
        String baseUrl = "http://localhost:" + env.getProperty("server.port", "8080")
                + env.getProperty("server.servlet.context-path", "");
        System.out.println("""

                ===================================================
                  base-pro 基础管理后台 启动成功!
                  后端地址: %s
                  接口文档: %s/doc.html
                ===================================================
                """.formatted(baseUrl, baseUrl));
    }
}
