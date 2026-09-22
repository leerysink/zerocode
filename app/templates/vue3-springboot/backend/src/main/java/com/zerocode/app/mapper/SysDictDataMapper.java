package com.zerocode.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.zerocode.app.entity.SysDictData;
import org.apache.ibatis.annotations.Mapper;

/**
 * 字典数据Mapper
 */
@Mapper
public interface SysDictDataMapper extends BaseMapper<SysDictData> {

    default SysDictData selectByTypeAndValue(String dictType, String dictValue) {
        return selectOne(new LambdaQueryWrapper<SysDictData>()
                .eq(SysDictData::getDictType, dictType)
                .eq(SysDictData::getDictValue, dictValue)
                .last("LIMIT 1"));
    }
}
