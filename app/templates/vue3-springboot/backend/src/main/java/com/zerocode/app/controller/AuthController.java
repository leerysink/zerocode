package com.zerocode.app.controller;

import com.zerocode.app.common.Result;
import com.zerocode.app.dto.LoginDTO;
import com.zerocode.app.dto.PasswordDTO;
import com.zerocode.app.dto.ProfileDTO;
import com.zerocode.app.service.AuthService;
import com.zerocode.app.service.SysMenuService;
import com.zerocode.app.vo.LoginVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 认证控制器
 */
@Tag(name = "认证管理")
@RestController
@RequestMapping("/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;
    private final SysMenuService sysMenuService;

    @Operation(summary = "登录")
    @PostMapping("/login")
    public Result<LoginVO> login(@Valid @RequestBody LoginDTO dto) {
        return Result.success(authService.login(dto));
    }

    @Operation(summary = "登出")
    @PostMapping("/logout")
    public Result<Void> logout() {
        authService.logout();
        return Result.success();
    }

    @Operation(summary = "获取当前用户信息")
    @GetMapping("/info")
    public Result<LoginVO> info() {
        return Result.success(authService.getUserInfo());
    }

    @Operation(summary = "获取当前用户路由菜单")
    @GetMapping("/routers")
    public Result<Object> routers() {
        return Result.success(sysMenuService.getRouters());
    }

    @Operation(summary = "修改当前用户密码")
    @PutMapping("/password")
    public Result<Void> updatePassword(@Valid @RequestBody PasswordDTO dto) {
        authService.updatePassword(dto);
        return Result.success();
    }

    @Operation(summary = "修改当前用户个人信息")
    @PutMapping("/profile")
    public Result<Void> updateProfile(@Valid @RequestBody ProfileDTO dto) {
        authService.updateProfile(dto);
        return Result.success();
    }

    @Operation(summary = "验证码占位接口")
    @GetMapping("/captcha")
    public Result<Map<String, String>> captcha() {
        return Result.success(Map.of("enabled", "false"));
    }
}
