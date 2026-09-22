package com.zerocode.app.vo;

import lombok.Data;

/**
 * 文件上传结果
 */
@Data
public class FileVO {

    /** 原始文件名 */
    private String fileName;

    /** 相对上传根目录的路径，形如 2026/09/09/uuid.png */
    private String filePath;

    /** 可直接访问的访问地址 */
    private String url;

    private Long fileSize;

    /** 扩展名（不含点） */
    private String fileType;

    /** SHA-256 内容哈希，供业务侧自行去重使用 */
    private String fileHash;
}
