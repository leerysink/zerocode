package com.zerocode.app.controller;

import com.zerocode.app.annotation.BusinessType;
import com.zerocode.app.annotation.Log;
import com.zerocode.app.common.PageResult;
import com.zerocode.app.common.Result;
import com.zerocode.app.dto.RoleMemberBindDTO;
import com.zerocode.app.dto.SysRoleDTO;
import com.zerocode.app.entity.SysRole;
import com.zerocode.app.service.SysRoleService;
import com.zerocode.app.vo.RoleMemberVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 角色管理控制器
 */
@Tag(name = "角色管理")
@RestController
@RequestMapping("/system/role")
@RequiredArgsConstructor
public class SysRoleController {

    private final SysRoleService sysRoleService;

    @Operation(summary = "角色分页列表")
    @GetMapping("/page")
    public Result<PageResult<SysRole>> page(
            @RequestParam(defaultValue = "1") Long current,
            @RequestParam(defaultValue = "10") Long size,
            @RequestParam(required = false) String roleName) {
        return Result.success(sysRoleService.page(current, size, roleName));
    }

    @Operation(summary = "全部可用角色")
    @GetMapping("/list")
    public Result<List<SysRole>> list() {
        return Result.success(sysRoleService.listAll());
    }

    @Operation(summary = "角色详情")
    @GetMapping("/{id}")
    public Result<SysRole> getById(@PathVariable Long id) {
        return Result.success(sysRoleService.getById(id));
    }

    @Operation(summary = "查询角色关联的菜单ID")
    @GetMapping("/{id}/menus")
    public Result<List<Long>> getMenuIds(@PathVariable Long id) {
        return Result.success(sysRoleService.getMenuIds(id));
    }

    @Operation(summary = "查询角色关联的部门ID")
    @GetMapping("/{id}/depts")
    public Result<List<Long>> getDeptIds(@PathVariable Long id) {
        return Result.success(sysRoleService.getDeptIds(id));
    }

    @Operation(summary = "查询角色已授权成员")
    @GetMapping("/{id}/members")
    public Result<List<RoleMemberVO>> members(@PathVariable Long id) {
        return Result.success(sysRoleService.members(id));
    }

    @Operation(summary = "查询角色可授权用户（未授权的启用用户）")
    @GetMapping("/{id}/member-candidates")
    public Result<List<RoleMemberVO>> memberCandidates(@PathVariable Long id) {
        return Result.success(sysRoleService.candidateMembers(id));
    }

    @Log(title = "角色人员管理", businessType = BusinessType.GRANT)
    @Operation(summary = "角色批量授权用户")
    @PostMapping("/{id}/members")
    public Result<Void> addMembers(@PathVariable Long id, @Valid @RequestBody RoleMemberBindDTO dto) {
        sysRoleService.addMembers(id, dto.getUserIds());
        return Result.success();
    }

    @Log(title = "角色人员管理", businessType = BusinessType.GRANT)
    @Operation(summary = "解除用户与该角色的关联")
    @DeleteMapping("/{id}/members/{userId}")
    public Result<Void> removeMember(@PathVariable Long id, @PathVariable Long userId) {
        sysRoleService.removeMember(id, userId);
        return Result.success();
    }

    @Log(title = "角色管理", businessType = BusinessType.INSERT)
    @Operation(summary = "新增角色")
    @PostMapping
    public Result<Void> add(@Valid @RequestBody SysRoleDTO dto) {
        sysRoleService.add(dto);
        return Result.success();
    }

    @Log(title = "角色管理", businessType = BusinessType.UPDATE)
    @Operation(summary = "修改角色")
    @PutMapping
    public Result<Void> update(@Valid @RequestBody SysRoleDTO dto) {
        sysRoleService.update(dto);
        return Result.success();
    }

    @Log(title = "角色管理", businessType = BusinessType.DELETE)
    @Operation(summary = "删除角色")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        sysRoleService.delete(id);
        return Result.success();
    }
}
