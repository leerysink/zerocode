package com.zerocode.app.controller;

import com.zerocode.app.common.Result;
import com.zerocode.app.dto.MobileRegisterDTO;
import com.zerocode.app.service.MobileAuthService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 移动端注册控制器
 *
 * <p>单独成类而不是往 {@link AuthController} 里加方法：基底控制器不因移动栈而分叉，
 * 该文件在安全配置里是 permitAll（登录前调用），与登录/登出同组
 */
@Tag(name = "移动端注册")
@RestController
@RequestMapping("/auth")
@RequiredArgsConstructor
public class MobileAuthController {

    private final MobileAuthService mobileAuthService;

    @Operation(summary = "移动端用户注册")
    @PostMapping("/register")
    public Result<Void> register(@Valid @RequestBody MobileRegisterDTO dto) {
        mobileAuthService.register(dto);
        return Result.success();
    }
}
