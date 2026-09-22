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
        // 应用名作 formatted 的实参，而不是写进格式串：名字里允许有 %（平台侧 check_name 刻意不拒，
        // 「50% 折扣」是正常业务名），裸 % 落在格式串里会被当成转换符，启动即抛 UnknownFormatConversionException。
        System.out.println("""

                ===================================================
                  %s 启动成功!
                  后端地址: %s
                ===================================================
                """.formatted("{{APP_NAME}}", baseUrl));
    }
}
