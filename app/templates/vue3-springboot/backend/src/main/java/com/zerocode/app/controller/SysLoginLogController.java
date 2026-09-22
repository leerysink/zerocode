package com.zerocode.app.controller;

import com.zerocode.app.common.PageResult;
import com.zerocode.app.common.Result;
import com.zerocode.app.entity.SysLoginLog;
import com.zerocode.app.service.SysLoginLogService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;

/**
 * 登录日志控制器
 */
@Tag(name = "登录日志")
@RestController
@RequestMapping("/system/loginLog")
@RequiredArgsConstructor
public class SysLoginLogController {

    private final SysLoginLogService sysLoginLogService;

    @Operation(summary = "登录日志分页列表")
    @GetMapping("/page")
    public Result<PageResult<SysLoginLog>> page(
            @RequestParam(defaultValue = "1") Long current,
            @RequestParam(defaultValue = "10") Long size,
            @RequestParam(required = false) String userName,
            @RequestParam(required = false) Integer status,
            @RequestParam(required = false) Integer loginType,
            @RequestParam(required = false) String ipaddr,
            @RequestParam(required = false) @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss") LocalDateTime beginTime,
            @RequestParam(required = false) @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss") LocalDateTime endTime) {
        return Result.success(sysLoginLogService.page(current, size, userName, status,
                loginType, ipaddr, beginTime, endTime));
    }

    @Operation(summary = "删除登录日志")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        sysLoginLogService.delete(id);
        return Result.success();
    }

    @Operation(summary = "清空登录日志")
    @DeleteMapping("/clean")
    public Result<Void> clean() {
        sysLoginLogService.clean();
        return Result.success();
    }
}
