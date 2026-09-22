package com.zerocode.app.controller;

import com.zerocode.app.annotation.BusinessType;
import com.zerocode.app.annotation.Log;
import com.zerocode.app.common.Result;
import com.zerocode.app.dto.SysDictTypeDTO;
import com.zerocode.app.entity.SysDictType;
import com.zerocode.app.service.SysDictTypeService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 字典类型控制器
 */
@Tag(name = "字典类型管理")
@RestController
@RequestMapping("/dict/type")
@RequiredArgsConstructor
public class SysDictTypeController {

    private final SysDictTypeService dictTypeService;

    @Operation(summary = "字典类型列表")
    @GetMapping("/list")
    public Result<List<SysDictType>> list(@RequestParam(required = false) String dictName) {
        return Result.success(dictTypeService.list(dictName));
    }

    @Log(title = "字典管理", businessType = BusinessType.INSERT)
    @Operation(summary = "新增字典类型")
    @PostMapping
    public Result<Void> add(@Valid @RequestBody SysDictTypeDTO dto) {
        dictTypeService.add(dto);
        return Result.success();
    }

    @Log(title = "字典管理", businessType = BusinessType.UPDATE)
    @Operation(summary = "修改字典类型")
    @PutMapping
    public Result<Void> update(@Valid @RequestBody SysDictTypeDTO dto) {
        dictTypeService.update(dto);
        return Result.success();
    }

    @Log(title = "字典管理", businessType = BusinessType.DELETE)
    @Operation(summary = "删除字典类型")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        dictTypeService.delete(id);
        return Result.success();
    }
}
