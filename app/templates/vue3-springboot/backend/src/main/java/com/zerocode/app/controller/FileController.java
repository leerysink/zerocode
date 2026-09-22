package com.zerocode.app.controller;

import com.zerocode.app.common.Result;
import com.zerocode.app.service.FileService;
import com.zerocode.app.vo.FileVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/**
 * 文件上传控制器
 */
@Tag(name = "文件管理")
@RestController
@RequestMapping("/file")
@RequiredArgsConstructor
public class FileController {

    private final FileService fileService;

    @Operation(summary = "上传文件")
    @PostMapping("/upload")
    public Result<FileVO> upload(@RequestParam("file") MultipartFile file) {
        return Result.success(fileService.upload(file));
    }
}
