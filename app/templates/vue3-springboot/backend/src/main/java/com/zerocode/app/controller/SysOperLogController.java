package com.zerocode.app.controller;

import com.zerocode.app.common.PageResult;
import com.zerocode.app.common.Result;
import com.zerocode.app.entity.SysOperLog;
import com.zerocode.app.service.SysOperLogService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;

/**
 * 操作日志控制器
 */
@Tag(name = "操作日志")
@RestController
@RequestMapping("/system/operLog")
@RequiredArgsConstructor
public class SysOperLogController {

    private final SysOperLogService sysOperLogService;

    @Operation(summary = "操作日志分页列表")
    @GetMapping("/page")
    public Result<PageResult<SysOperLog>> page(
            @RequestParam(defaultValue = "1") Long current,
            @RequestParam(defaultValue = "10") Long size,
            @RequestParam(required = false) String title,
            @RequestParam(required = false) Integer businessType,
            @RequestParam(required = false) Integer status,
            @RequestParam(required = false) String operUserName,
            @RequestParam(required = false) @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss") LocalDateTime beginTime,
            @RequestParam(required = false) @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss") LocalDateTime endTime) {
        return Result.success(sysOperLogService.page(current, size, title, businessType,
                status, operUserName, beginTime, endTime));
    }

    @Operation(summary = "删除操作日志")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        sysOperLogService.delete(id);
        return Result.success();
    }

    @Operation(summary = "清空操作日志")
    @DeleteMapping("/clean")
    public Result<Void> clean() {
        sysOperLogService.clean();
        return Result.success();
    }
}
