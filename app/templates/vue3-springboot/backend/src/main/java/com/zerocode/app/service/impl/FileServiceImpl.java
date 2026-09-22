package com.zerocode.app.service.impl;

import com.zerocode.app.common.BusinessException;
import com.zerocode.app.service.FileService;
import com.zerocode.app.vo.FileVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

/**
 * 文件服务实现
 */
@Slf4j
@Service
public class FileServiceImpl implements FileService {

    @Value("${file.upload-dir}")
    private String uploadDir;

    @Value("${file.allowed-types}")
    private String allowedTypes;

    @Value("${file.max-size}")
    private long maxSize;

    /**
     * 上传文件
     */
    @Override
    public FileVO upload(MultipartFile file) {
        if (file.isEmpty()) {
            throw BusinessException.of("上传文件不能为空");
        }
        if (file.getSize() > maxSize) {
            throw BusinessException.of("上传文件大小超过限制");
        }

        String originalName = file.getOriginalFilename();
        String suffix = getSuffix(originalName);
        List<String> allowed = Arrays.asList(allowedTypes.split(","));
        if (!allowed.contains(suffix.toLowerCase())) {
            throw BusinessException.of("不支持的文件类型: " + suffix);
        }

        // 计算文件哈希（仅作为结果返回，供业务侧自行去重；上传本身不再拒绝重复内容）
        String fileHash = computeHash(file);

        // 按日期分目录
        String dateDir = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyy/MM/dd"));
        String dirPath = uploadDir + File.separator + dateDir;
        File dir = new File(dirPath);
        if (!dir.exists() && !dir.mkdirs()) {
            throw BusinessException.of("创建上传目录失败");
        }

        // 生成新文件名
        String newFileName = UUID.randomUUID().toString().replace("-", "") + "." + suffix;
        File dest = new File(dir, newFileName);
        try {
            file.transferTo(dest);
        } catch (IOException e) {
            log.error("文件上传失败", e);
            throw BusinessException.of("文件上传失败");
        }

        FileVO vo = new FileVO();
        vo.setFileName(originalName);
        vo.setFilePath(dateDir + "/" + newFileName);
        vo.setUrl("/uploads/" + dateDir + "/" + newFileName);
        vo.setFileSize(file.getSize());
        vo.setFileType(suffix);
        vo.setFileHash(fileHash);

        log.info("文件上传成功: {} -> {}", originalName, vo.getFilePath());
        return vo;
    }

    private String computeHash(MultipartFile file) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            try (DigestInputStream dis = new DigestInputStream(file.getInputStream(), md)) {
                byte[] buffer = new byte[8192];
                while (dis.read(buffer) != -1) {
                    // 持续读取并计算摘要
                }
            }
            byte[] digest = md.digest();
            StringBuilder sb = new StringBuilder();
            for (byte b : digest) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException | IOException e) {
            log.error("计算文件哈希失败", e);
            throw BusinessException.of("文件处理失败");
        }
    }

    private String getSuffix(String fileName) {
        if (fileName == null || !fileName.contains(".")) {
            return "";
        }
        return fileName.substring(fileName.lastIndexOf(".") + 1);
    }

    /**
     * 获取上传根目录
     */
    @Override
    public String getUploadDir() {
        return uploadDir;
    }
}
