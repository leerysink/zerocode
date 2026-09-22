package com.zerocode.app.service;

import com.zerocode.app.dto.SysDictDataDTO;
import com.zerocode.app.entity.SysDictData;

import java.util.List;

/**
 * 字典数据服务
 */
public interface SysDictDataService {

    List<SysDictData> list(String dictType);

    /**
     * 根据字典类型编码获取有效字典项（前端下拉用）
     */
    List<SysDictData> listByType(String dictType);

    void add(SysDictDataDTO dto);

    void update(SysDictDataDTO dto);

    void delete(Long id);
}
