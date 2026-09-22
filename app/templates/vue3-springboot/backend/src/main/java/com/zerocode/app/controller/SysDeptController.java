package com.zerocode.app.controller;

import com.zerocode.app.annotation.BusinessType;
import com.zerocode.app.annotation.Log;
import com.zerocode.app.common.Result;
import com.zerocode.app.dto.SysDeptDTO;
import com.zerocode.app.entity.SysDept;
import com.zerocode.app.service.SysDeptService;
import com.zerocode.app.vo.TreeNodeVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 部门管理控制器
 */
@Tag(name = "部门管理")
@RestController
@RequestMapping("/system/dept")
@RequiredArgsConstructor
public class SysDeptController {

    private final SysDeptService sysDeptService;

    @Operation(summary = "部门列表")
    @GetMapping("/list")
    public Result<List<SysDept>> list(@RequestParam(required = false) String deptName,
                                      @RequestParam(required = false) Integer status) {
        return Result.success(sysDeptService.list(deptName, status));
    }

    @Operation(summary = "部门树")
    @GetMapping("/tree")
    public Result<List<TreeNodeVO>> tree() {
        return Result.success(sysDeptService.tree());
    }

    @Operation(summary = "部门详情")
    @GetMapping("/{id}")
    public Result<SysDept> getById(@PathVariable Long id) {
        return Result.success(sysDeptService.getById(id));
    }

    @Log(title = "部门管理", businessType = BusinessType.INSERT)
    @Operation(summary = "新增部门")
    @PostMapping
    public Result<Void> add(@Valid @RequestBody SysDeptDTO dto) {
        sysDeptService.add(dto);
        return Result.success();
    }

    @Log(title = "部门管理", businessType = BusinessType.UPDATE)
    @Operation(summary = "修改部门")
    @PutMapping
    public Result<Void> update(@Valid @RequestBody SysDeptDTO dto) {
        sysDeptService.update(dto);
        return Result.success();
    }

    @Log(title = "部门管理", businessType = BusinessType.DELETE)
    @Operation(summary = "删除部门")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        sysDeptService.delete(id);
        return Result.success();
    }
}
