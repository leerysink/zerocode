package com.zerocode.app.controller;

import com.zerocode.app.annotation.BusinessType;
import com.zerocode.app.annotation.Log;
import com.zerocode.app.common.Result;
import com.zerocode.app.dto.SysMenuDTO;
import com.zerocode.app.entity.SysMenu;
import com.zerocode.app.service.SysMenuService;
import com.zerocode.app.vo.TreeNodeVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 菜单管理控制器
 */
@Tag(name = "菜单管理")
@RestController
@RequestMapping("/system/menu")
@RequiredArgsConstructor
public class SysMenuController {

    private final SysMenuService sysMenuService;

    @Operation(summary = "菜单列表")
    @GetMapping("/list")
    public Result<List<SysMenu>> list(@RequestParam(required = false) String menuName) {
        return Result.success(sysMenuService.list(menuName));
    }

    @Operation(summary = "菜单树")
    @GetMapping("/tree")
    public Result<List<TreeNodeVO>> tree() {
        return Result.success(sysMenuService.tree());
    }

    @Log(title = "菜单管理", businessType = BusinessType.INSERT)
    @Operation(summary = "新增菜单")
    @PostMapping
    public Result<Void> add(@Valid @RequestBody SysMenuDTO dto) {
        sysMenuService.add(dto);
        return Result.success();
    }

    @Log(title = "菜单管理", businessType = BusinessType.UPDATE)
    @Operation(summary = "修改菜单")
    @PutMapping
    public Result<Void> update(@Valid @RequestBody SysMenuDTO dto) {
        sysMenuService.update(dto);
        return Result.success();
    }

    @Log(title = "菜单管理", businessType = BusinessType.DELETE)
    @Operation(summary = "删除菜单")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        sysMenuService.delete(id);
        return Result.success();
    }
}
