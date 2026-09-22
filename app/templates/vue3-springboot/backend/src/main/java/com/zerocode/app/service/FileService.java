package com.zerocode.app.service;

import com.zerocode.app.vo.FileVO;
import org.springframework.web.multipart.MultipartFile;

/**
 * 文件服务
 */
public interface FileService {

    /**
     * 上传文件
     */
    FileVO upload(MultipartFile file);

    /**
     * 获取上传根目录
     */
    String getUploadDir();
}
