package com.zerocode.app.service;

import com.zerocode.app.dto.SysDictTypeDTO;
import com.zerocode.app.entity.SysDictType;

import java.util.List;

/**
 * 字典类型服务
 */
public interface SysDictTypeService {

    List<SysDictType> list(String dictName);

    void add(SysDictTypeDTO dto);

    void update(SysDictTypeDTO dto);

    void delete(Long id);
}
