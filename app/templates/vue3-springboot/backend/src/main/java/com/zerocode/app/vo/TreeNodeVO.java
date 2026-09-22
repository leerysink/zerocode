package com.zerocode.app.vo;

import lombok.Data;

import java.util.ArrayList;
import java.util.List;

/**
 * 树形节点VO（菜单 / 部门等层级结构通用）
 */
@Data
public class TreeNodeVO {

    private Long id;

    private Long parentId;

    private String label;

    private List<TreeNodeVO> children = new ArrayList<>();

    public void addChild(TreeNodeVO child) {
        this.children.add(child);
    }
}
