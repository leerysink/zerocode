package com.zerocode.app.config;

import com.zerocode.app.service.FileService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.io.File;

/**
 * Web MVC 配置（静态资源映射）
 */
@Configuration
@RequiredArgsConstructor
public class WebMvcConfig implements WebMvcConfigurer {

    private final FileService fileService;

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        String uploadDir = fileService.getUploadDir();
        String location = "file:" + uploadDir + File.separator;
        registry.addResourceHandler("/uploads/**")
                .addResourceLocations(location);
    }
}
