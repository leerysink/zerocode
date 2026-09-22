package com.zerocode.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.zerocode.app.entity.SysDept;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 部门Mapper
 */
@Mapper
public interface SysDeptMapper extends BaseMapper<SysDept> {

    /**
     * 查询部门及其所有子孙部门ID（递归 CTE，含自身）
     */
    List<Long> selectDescendantIds(@Param("deptId") Long deptId);

    /**
     * 统计未删除的子部门数量
     */
    Integer countChildren(@Param("deptId") Long deptId);
}
