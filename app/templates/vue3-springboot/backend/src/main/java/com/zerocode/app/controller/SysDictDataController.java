package com.zerocode.app.controller;

import com.zerocode.app.annotation.BusinessType;
import com.zerocode.app.annotation.Log;
import com.zerocode.app.common.Result;
import com.zerocode.app.dto.SysDictDataDTO;
import com.zerocode.app.entity.SysDictData;
import com.zerocode.app.service.SysDictDataService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 字典数据控制器
 */
@Tag(name = "字典数据管理")
@RestController
@RequestMapping("/dict/data")
@RequiredArgsConstructor
public class SysDictDataController {

    private final SysDictDataService dictDataService;

    @Operation(summary = "字典项列表（按类型编码）")
    @GetMapping("/list")
    public Result<List<SysDictData>> list(@RequestParam String dictType) {
        return Result.success(dictDataService.list(dictType));
    }

    @Operation(summary = "根据字典类型获取有效字典项（前端下拉用）")
    @GetMapping("/by-type/{dictType}")
    public Result<List<SysDictData>> byType(@PathVariable String dictType) {
        return Result.success(dictDataService.listByType(dictType));
    }

    @Log(title = "字典管理", businessType = BusinessType.INSERT)
    @Operation(summary = "新增字典项")
    @PostMapping
    public Result<Void> add(@Valid @RequestBody SysDictDataDTO dto) {
        dictDataService.add(dto);
        return Result.success();
    }

    @Log(title = "字典管理", businessType = BusinessType.UPDATE)
    @Operation(summary = "修改字典项")
    @PutMapping
    public Result<Void> update(@Valid @RequestBody SysDictDataDTO dto) {
        dictDataService.update(dto);
        return Result.success();
    }

    @Log(title = "字典管理", businessType = BusinessType.DELETE)
    @Operation(summary = "删除字典项")
    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        dictDataService.delete(id);
        return Result.success();
    }
}
