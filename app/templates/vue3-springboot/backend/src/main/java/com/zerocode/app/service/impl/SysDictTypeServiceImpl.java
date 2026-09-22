package com.zerocode.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.zerocode.app.common.BusinessException;
import com.zerocode.app.dto.SysDictTypeDTO;
import com.zerocode.app.entity.SysDictData;
import com.zerocode.app.entity.SysDictType;
import com.zerocode.app.mapper.SysDictDataMapper;
import com.zerocode.app.mapper.SysDictTypeMapper;
import com.zerocode.app.service.SysDictTypeService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.List;

/**
 * 字典类型服务实现
 */
@Service
@RequiredArgsConstructor
public class SysDictTypeServiceImpl implements SysDictTypeService {

    private final SysDictTypeMapper dictTypeMapper;
    private final SysDictDataMapper dictDataMapper;

    @Override
    public List<SysDictType> list(String dictName) {
        LambdaQueryWrapper<SysDictType> wrapper = new LambdaQueryWrapper<>();
        wrapper.like(StringUtils.hasText(dictName), SysDictType::getDictName, dictName);
        wrapper.orderByAsc(SysDictType::getId);
        return dictTypeMapper.selectList(wrapper);
    }

    @Override
    public void add(SysDictTypeDTO dto) {
        // 唯一性校验
        LambdaQueryWrapper<SysDictType> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysDictType::getDictType, dto.getDictType());
        if (dictTypeMapper.selectCount(wrapper) > 0) {
            throw BusinessException.of("字典类型编码已存在");
        }
        SysDictType entity = new SysDictType();
        BeanUtils.copyProperties(dto, entity);
        if (entity.getStatus() == null) entity.setStatus(1);
        dictTypeMapper.insert(entity);
    }

    @Override
    public void update(SysDictTypeDTO dto) {
        if (dto.getId() == null) {
            throw BusinessException.of("字典类型ID不能为空");
        }
        // 唯一性校验（排除自身）
        LambdaQueryWrapper<SysDictType> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysDictType::getDictType, dto.getDictType());
        wrapper.ne(SysDictType::getId, dto.getId());
        if (dictTypeMapper.selectCount(wrapper) > 0) {
            throw BusinessException.of("字典类型编码已存在");
        }
        SysDictType entity = new SysDictType();
        BeanUtils.copyProperties(dto, entity);
        dictTypeMapper.updateById(entity);
    }

    @Override
    public void delete(Long id) {
        SysDictType type = dictTypeMapper.selectById(id);
        if (type == null) {
            throw BusinessException.of("字典类型不存在");
        }
        // 检查是否有关联字典数据
        LambdaQueryWrapper<SysDictData> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysDictData::getDictType, type.getDictType());
        if (dictDataMapper.selectCount(wrapper) > 0) {
            throw BusinessException.of("该字典类型下存在字典项，无法删除，请先删除字典项");
        }
        dictTypeMapper.deleteById(id);
    }
}
