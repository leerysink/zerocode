package com.zerocode.app.controller;

import com.zerocode.app.annotation.BusinessType;
import com.zerocode.app.annotation.Log;
import com.zerocode.app.common.PageResult;
import com.zerocode.app.common.Result;
import com.zerocode.app.dto.SysUserDTO;
import com.zerocode.app.dto.SysUserQueryDTO;
import com.zerocode.app.entity.SysUser;
import com.zerocode.app.service.SysUserService;
import com.zerocode.app.vo.SysUserVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 用户管理控制器
 */
@Tag(name = "用户管理")
@RestController
@RequestMapping("/system/user")
@RequiredArgsConstructor
public class SysUserController {

    private final SysUserService sysUserService;

    /**
     * 同样用 DTO 接参而不是无参：service 上的 @DataScope 需要一个实现
     * {@link com.zerocode.app.aspect.DataScopeQuery} 的入参来接可见部门集合，
     * 没有这个参数切面就无法把列表收敛到本部门范围内
     */
    @Operation(summary = "用户列表（全部启用的，用于下拉选择）")
    @GetMapping("/list")
    public Result<List<SysUser>> list(SysUserQueryDTO query) {
        return Result.success(sysUserService.list(query));
    }

    /**
     * query 不加 @RequestBody，由 Spring MVC 按 query 参数绑定（?username=x&status=1），
     * 前端调用方式与改造前一致；可见部门集合由 service 上的 @DataScope 切面注入
     */
    @Operation(summary = "用户分页列表")
    @GetMapping("/page")
    public Result<PageResult<SysUserVO>> page(
            @RequestParam(defaultValue = "1") Long current,
            @RequestParam(defaultValue = "10") Long size,
            SysUserQueryDTO query) {
        return Result.success(sysUserService.page(current, size, query));
    }

    @Operation(summary = "用户详情")
    @GetMapping("/{id}")
    public Result<SysUser> getById(@PathVariable Long id) {
        return Result.success(sysUserService.getById(id));
    }

    @Operation(summary = "查询用户角色ID集合")
    @GetMapping("/{id}/roles")
    public Result<List<Long>> getRoleIds(@PathVariable Long id) {
        return Result.success(sysUserService.getRoleIds(id));
    }

    @Log(title = "用户管理", businessType = BusinessType.INSERT)
    @Operation(summary = "新增用户")
    @PostMapping
    public Result<Void> add(@Valid @RequestBody SysUserDTO dto) {
        sysUserService.add(dto);
        return Result.success();
    }

    @Log(title = "用户管理", businessType = BusinessType.UPDATE)
    @Operation(summary = "修改用户")
    @PutMapping
    public Result<Void> update(@Valid @RequestBody SysUserDTO dto) {
        sysUserService.update(dto);
        return Result.success();
    }

    @Log(title = "用户管理", businessType = BusinessType.DELETE)
    @Operation(summary = "删除用户")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        sysUserService.delete(id);
        return Result.success();
    }

    @Log(title = "用户管理", businessType = BusinessType.UPDATE)
    @Operation(summary = "重置密码")
    @PutMapping("/{id}/reset-password")
    public Result<Void> resetPassword(@PathVariable Long id, @RequestParam String newPassword) {
        sysUserService.resetPassword(id, newPassword);
        return Result.success();
    }
}
