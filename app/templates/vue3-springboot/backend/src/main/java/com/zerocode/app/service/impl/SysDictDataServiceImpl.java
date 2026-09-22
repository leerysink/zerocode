package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.zerocode.app.common.BusinessException;
import com.zerocode.app.dto.SysDictDataDTO;
import com.zerocode.app.entity.SysDictData;
import com.zerocode.app.mapper.SysDictDataMapper;
import com.zerocode.app.service.SysDictDataService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 字典数据服务实现
 */
@Service
@RequiredArgsConstructor
public class SysDictDataServiceImpl implements SysDictDataService {

    private final SysDictDataMapper dictDataMapper;

    @Override
    public List<SysDictData> list(String dictType) {
        LambdaQueryWrapper<SysDictData> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysDictData::getDictType, dictType);
        wrapper.orderByAsc(SysDictData::getSort);
        return dictDataMapper.selectList(wrapper);
    }

    /**
     * 根据字典类型编码获取有效字典项（前端下拉用）
     */
    @Override
    public List<SysDictData> listByType(String dictType) {
        LambdaQueryWrapper<SysDictData> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysDictData::getDictType, dictType);
        wrapper.eq(SysDictData::getStatus, 1);
        wrapper.orderByAsc(SysDictData::getSort);
        return dictDataMapper.selectList(wrapper);
    }

    @Override
    public void add(SysDictDataDTO dto) {
        SysDictData entity = new SysDictData();
        BeanUtils.copyProperties(dto, entity);
        if (entity.getStatus() == null) entity.setStatus(1);
        if (entity.getSort() == null) entity.setSort(0);
        dictDataMapper.insert(entity);
    }

    @Override
    public void update(SysDictDataDTO dto) {
        if (dto.getId() == null) {
            throw BusinessException.of("字典数据ID不能为空");
        }
        SysDictData entity = new SysDictData();
        BeanUtils.copyProperties(dto, entity);
        dictDataMapper.updateById(entity);
    }

    @Override
    public void delete(Long id) {
        dictDataMapper.deleteById(id);
    }
}
