<template>
  <div>
    <a-card :bordered="false" class="page-card">
      <div class="search-bar">
        <a-input v-model:value="menuName" placeholder="菜单名称" allow-clear style="width: 200px" />
        <a-button type="primary" @click="loadData">
          <SearchOutlined /> 查询
        </a-button>
        <a-button @click="loadData">重置</a-button>
      </div>

      <div class="action-bar">
        <div class="action-title-group">
          <span class="page-title">
            <MenuOutlined /> 菜单管理
          </span>
          <a-tag color="blue">共 {{ pagination.total }} 条</a-tag>
        </div>
        <a-button type="primary" size="large" class="add-btn" @click="handleAdd(null)">
          <PlusOutlined /> 新增菜单
        </a-button>
      </div>

      <a-table
        bordered
        size="small"
        :columns="columns"
        :data-source="treeData"
        :loading="loading"
        row-key="id"
        :pagination="false"
        v-model:expanded-row-keys="expandedRowKeys"
        :indent-size="20"
        :expand-column-width="48"
        class="tree-table menu-table"
      >
        <template #expandColumnTitle>
          <span class="expand-col-title"><CaretDownOutlined /></span>
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'menuName'">
            <span class="menu-name">
              <div class="menu-icon-wrap">
                <component :is="getIcon(record.icon)" />
              </div>
              <span class="menu-name-text">{{ record.menuName }}</span>
              <a-tag v-if="record.parentId === 0 && record.menuType === 'M'" color="blue" class="menu-level-tag">目录</a-tag>
              <a-tag v-else-if="record.menuType === 'C'" color="cyan" class="menu-level-tag">菜单</a-tag>
              <a-tag v-else-if="record.menuType === 'F'" color="orange" class="menu-level-tag">按钮</a-tag>
            </span>
          </template>
          <template v-if="column.dataIndex === 'permission'">
            <span class="code-cell">{{ record.permission || '-' }}</span>
          </template>
          <template v-if="column.dataIndex === 'visible'">
            <a-tag :color="record.visible === 1 ? 'success' : 'default'" class="status-tag">
              <span class="status-dot" :class="record.visible === 1 ? 'on' : 'off'"></span>
              {{ record.visible === 1 ? '显示' : '隐藏' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="record.status === 1 ? 'success' : 'default'" class="status-tag">
              <span class="status-dot" :class="record.status === 1 ? 'on' : 'off'"></span>
              {{ record.status === 1 ? '启用' : '停用' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'action'">
            <a-space :size="4">
              <a-button type="link" size="small" @click="handleAdd(record)">
                <PlusOutlined /> 新增子菜单
              </a-button>
              <a-button type="link" size="small" @click="handleEdit(record)">
                <EditOutlined /> 编辑
              </a-button>
              <a-popconfirm
                title="确认删除菜单?"
                ok-text="确定"
                cancel-text="取消"
                @confirm="handleDelete(record)"
              >
                <a-button type="link" size="small" danger>
                  <DeleteOutlined /> 删除
                </a-button>
              </a-popconfirm>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-modal
      v-model:open="modalVisible"
      :title="isEdit ? '编辑菜单' : '新增菜单'"
      :footer="null"
      :width="720"
      :destroy-on-close="true"
      class="form-modal"
    >
      <div class="modal-header">
        <div class="modal-header-icon"><MenuOutlined /></div>
        <div>
          <div class="modal-header-title">{{ isEdit ? '编辑菜单' : '新增菜单' }}</div>
          <div class="modal-header-subtitle">配置菜单节点、路由和权限标识</div>
        </div>
      </div>
      <a-form :model="form" layout="vertical" :rules="rules" @finish="handleSubmit">
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="上级菜单">
              <a-tree-select v-model:value="form.parentId" :tree-data="treeOptions" placeholder="无（顶级）" allow-clear
                tree-default-expand-all :field-names="{ label: 'menuName', value: 'id', children: 'children' }" size="large" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="菜单类型" name="menuType">
              <a-radio-group v-model:value="form.menuType" button-style="solid" size="large">
                <a-radio-button value="M">目录</a-radio-button>
                <a-radio-button value="C">菜单</a-radio-button>
                <a-radio-button value="F">按钮</a-radio-button>
              </a-radio-group>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="菜单名称" name="menuName">
              <a-input v-model:value="form.menuName" placeholder="菜单显示名称" size="large" allow-clear />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="排序">
              <a-input-number v-model:value="form.sort" :min="0" style="width: 100%" size="large" />
            </a-form-item>
          </a-col>
          <a-col :span="12" v-if="form.menuType !== 'F'">
            <a-form-item label="路由地址">
              <a-input v-model:value="form.path" placeholder="如：user" size="large" allow-clear>
                <template #prefix><LinkOutlined /></template>
              </a-input>
            </a-form-item>
          </a-col>
          <a-col :span="12" v-if="form.menuType === 'C'">
            <a-form-item label="组件路径">
              <a-input v-model:value="form.component" placeholder="如：system/user/index" size="large" allow-clear />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="权限标识">
              <a-input v-model:value="form.permission" placeholder="如：system:user:list" size="large" allow-clear>
                <template #prefix><KeyOutlined /></template>
              </a-input>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="图标">
              <a-input v-model:value="form.icon" placeholder="如：UserOutlined" size="large" allow-clear>
                <template #prefix><SmileOutlined /></template>
              </a-input>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="是否显示">
              <a-radio-group v-model:value="form.visible" button-style="solid" size="large">
                <a-radio-button :value="1">显示</a-radio-button>
                <a-radio-button :value="0">隐藏</a-radio-button>
              </a-radio-group>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="状态">
              <a-radio-group v-model:value="form.status" button-style="solid" size="large">
                <a-radio-button :value="1">启用</a-radio-button>
                <a-radio-button :value="0">停用</a-radio-button>
              </a-radio-group>
            </a-form-item>
          </a-col>
        </a-row>
        <div class="modal-footer">
          <a-button @click="modalVisible = false" size="large">取消</a-button>
          <a-button type="primary" html-type="submit" :loading="submitting" size="large">
            <SaveOutlined v-if="isEdit" />
            <PlusOutlined v-else />
            {{ isEdit ? '保存修改' : '确认添加' }}
          </a-button>
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import * as AntIcons from '@ant-design/icons-vue'
import {
  MenuOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  LinkOutlined,
  KeyOutlined,
  SmileOutlined,
  SearchOutlined,
  SaveOutlined,
  CaretDownOutlined,
} from '@ant-design/icons-vue'
import { menuList, menuAdd, menuUpdate, menuDelete, type SysMenu, type TreeNode } from '@/api/menu'

const loading = ref(false)
const submitting = ref(false)
const treeData = ref<any[]>([])
const treeOptions = ref<TreeNode[]>([])
// 受控展开集：a-table 的 default-expand-all-rows 在 watchEffect 里算一次就 stop，
// 而菜单数据是 onMounted 异步到的（首帧为空），所以它永远展开不了。同部门管理页。
const expandedRowKeys = ref<number[]>([])
const modalVisible = ref(false)
const isEdit = ref(false)
const menuName = ref('')

const form = reactive<Partial<SysMenu>>({ parentId: 0, menuType: 'C', sort: 0, visible: 1, status: 1 })
const rules = { menuName: [{ required: true, message: '请输入菜单名称' }], menuType: [{ required: true, message: '请选择类型' }] }

const pagination = reactive({ total: 0 })

const columns = [
  { title: '菜单名称', dataIndex: 'menuName', minWidth: 240 },
  { title: '权限标识', dataIndex: 'permission', width: 200 },
  { title: '排序', dataIndex: 'sort', width: 80, align: 'center' as const },
  { title: '显示', dataIndex: 'visible', width: 110, align: 'center' as const },
  { title: '状态', dataIndex: 'status', width: 110, align: 'center' as const },
  { title: '操作', dataIndex: 'action', width: 300, fixed: 'right' as const },
] 

// 图标名是表单里自由输入的字符串，这里按名从整包解析（与 layout 同一做法），
// 未识别的名字退回默认图标。不能用手写白名单：种子里的 ApartmentOutlined /
// BookOutlined / LoginOutlined 不在白名单时，菜单表会把它错显成默认图标且不报错。
function getIcon(name?: string) {
  if (!name) return MenuOutlined
  return (AntIcons as any)[name] || MenuOutlined
}

/**
 * 构建树形结构并清理空 children 字段
 * 删除空 children 后，ant-design-vue 不会为叶子节点渲染 + 号
 */
function buildTree(list: SysMenu[]): any[] {
  const map = new Map<number, any>()
  list.forEach((item) => map.set(item.id, { ...item }))
  const roots: any[] = []
  map.forEach((node) => {
    if (node.parentId && map.has(node.parentId)) {
      const parent = map.get(node.parentId)
      if (!parent.children) parent.children = []
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  })
  // 清理空 children，让叶子节点不显示 + 号
  const cleanup = (nodes: any[]) => {
    nodes.forEach((node) => {
      if (node.children && node.children.length === 0) {
        delete node.children
      } else if (node.children) {
        cleanup(node.children)
      }
    })
  }
  cleanup(roots)
  return roots
}

/**
 * 收集所有「有子节点」的行 id 作为展开集。buildTree 已把空 children 删掉，
 * 所以这里只需看 children 字段在不在。
 */
function collectExpandableKeys(nodes: any[]): number[] {
  const keys: number[] = []
  const walk = (list: any[]) => {
    list.forEach((node) => {
      if (node.children && node.children.length > 0) {
        keys.push(node.id)
        walk(node.children)
      }
    })
  }
  walk(nodes)
  return keys
}

async function loadData() {
  loading.value = true
  try {
    const res = await menuList(menuName.value || undefined)
    const tree = buildTree(res.data)
    treeData.value = tree
    expandedRowKeys.value = collectExpandableKeys(tree)
    treeOptions.value = JSON.parse(JSON.stringify(tree))
    pagination.total = res.data.length
  } finally { loading.value = false }
}

function handleAdd(record: SysMenu | null) {
  isEdit.value = false
  Object.assign(form, { id: undefined, parentId: record ? record.id : 0, menuName: '', menuType: 'C', path: '', component: '', permission: '', icon: '', sort: 0, visible: 1, status: 1 })
  modalVisible.value = true
}

function handleEdit(record: SysMenu) {
  isEdit.value = true
  Object.assign(form, record)
  modalVisible.value = true
}

async function handleSubmit() {
  submitting.value = true
  try {
    if (isEdit.value) { await menuUpdate(form); message.success('修改成功') }
    else { await menuAdd(form); message.success('新增成功') }
    modalVisible.value = false
    loadData()
  } finally { submitting.value = false }
}

async function handleDelete(record: SysMenu) {
  await menuDelete(record.id)
  message.success('删除成功')
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.page-card { background: #fff !important; border-radius: var(--radius-md) !important; }

.search-bar {
  display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  padding: 16px 20px; background: linear-gradient(to right, #f8fafc 0%, #ffffff 100%);
  border-radius: var(--radius-md); margin-bottom: 16px;
  border: 1px solid var(--border-color);
}

.action-bar {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 16px; padding: 0 4px;
}
.action-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-title {
  font-size: 18px; font-weight: 600; color: var(--text-primary);
  display: inline-flex; align-items: center; gap: 8px;
}
.page-title :deep(.anticon) { color: var(--primary-color); font-size: 20px; }

.add-btn {
  background: var(--primary-gradient) !important;
  border-color: transparent !important;
  box-shadow: 0 4px 14px rgba(29, 78, 255, 0.35);
}

/* ---------- 树表格通用美化（框线与网格线已由 a-table bordered + 全局 theme 统一提供） ---------- */
:deep(.tree-table .ant-table-thead > tr > th) {
  background: var(--table-head-bg) !important;
  font-weight: 600 !important;
  color: var(--table-head-color) !important;
  letter-spacing: 0.3px;
}

:deep(.tree-table .ant-table-tbody > tr > td) {
  vertical-align: middle !important;
}

:deep(.tree-table .ant-table-tbody > tr:hover > td) {
  background: #f8faff !important;
}

:deep(.tree-table .ant-table-tbody > tr:hover .menu-icon-wrap) {
  transform: scale(1.08);
  box-shadow: 0 4px 12px rgba(29, 78, 255, 0.3);
}

/* 重写展开按钮：仅对非叶子节点生效 */
:deep(.tree-table .ant-table-row-expand-icon-collapsed),
:deep(.tree-table .ant-table-row-expand-icon-expanded) {
  width: 22px !important;
  height: 22px !important;
  min-width: 22px !important;
  border-radius: 50% !important;
  background: #fff !important;
  border: 1px solid #d1d5db !important;
  color: var(--text-muted) !important;
  margin-right: 10px !important;
  margin-left: 4px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 0 !important;
  transition: all 0.2s ease !important;
  vertical-align: middle !important;
  line-height: 1 !important;
  font-size: 0 !important;
  position: relative !important;
  cursor: pointer;
  outline: none;
}

:deep(.tree-table .ant-table-row-expand-icon-collapsed:hover),
:deep(.tree-table .ant-table-row-expand-icon-expanded:hover) {
  border-color: var(--primary-color) !important;
  color: var(--primary-color) !important;
  background: var(--primary-gradient-soft) !important;
  box-shadow: 0 0 0 3px rgba(29, 78, 255, 0.12) !important;
}

/* 加减号：横向杠常驻，纵向杠仅折叠时显示 → 折叠为 +，展开为 − */
:deep(.tree-table .ant-table-row-expand-icon-collapsed::before),
:deep(.tree-table .ant-table-row-expand-icon-expanded::before),
:deep(.tree-table .ant-table-row-expand-icon-collapsed::after),
:deep(.tree-table .ant-table-row-expand-icon-expanded::after) {
  content: '' !important;
  position: absolute !important;
  top: 50% !important;
  left: 50% !important;
  margin: 0 !important;
  background: currentColor !important;
  border: none !important;
  border-radius: 1px !important;
  transition: transform 0.2s ease, opacity 0.2s ease !important;
}

/* 横向杠（− 的那一横） */
:deep(.tree-table .ant-table-row-expand-icon-collapsed::before),
:deep(.tree-table .ant-table-row-expand-icon-expanded::before) {
  width: 10px !important;
  height: 1.6px !important;
  transform: translate(-50%, -50%) !important;
}

/* 纵向杠（+ 的那一竖）：折叠显示，展开收起 */
:deep(.tree-table .ant-table-row-expand-icon-collapsed::after) {
  width: 1.6px !important;
  height: 10px !important;
  transform: translate(-50%, -50%) !important;
  opacity: 1 !important;
}
:deep(.tree-table .ant-table-row-expand-icon-expanded::after) {
  width: 1.6px !important;
  height: 10px !important;
  transform: translate(-50%, -50%) scaleY(0) !important;
  opacity: 0 !important;
}

/* 叶子节点 spaced：保留缩进占位，无视觉 */
:deep(.tree-table .ant-table-row-expand-icon-spaced) {
  width: 22px !important;
  height: 22px !important;
  min-width: 22px !important;
  margin-right: 10px !important;
  margin-left: 4px !important;
  background: transparent !important;
  border: none !important;
  cursor: default !important;
  display: inline-block !important;
  vertical-align: middle !important;
  padding: 0 !important;
  box-sizing: border-box;
}
:deep(.tree-table .ant-table-row-expand-icon-spaced:hover) {
  background: transparent !important;
  box-shadow: none !important;
}

/* 子行背景淡化 */
:deep(.tree-table .ant-table-expanded-row > td) {
  background: #fafbfc !important;
}
:deep(.tree-table .ant-table-expanded-row:hover > td) {
  background: #f0f5ff !important;
}

/* ---------- 菜单单元格 ---------- */
:deep(.menu-icon-wrap) {
  width: 36px; height: 36px; border-radius: 10px;
  background: var(--primary-gradient-soft);
  color: var(--primary-color);
  display: flex; align-items: center; justify-content: center; font-size: 16px;
  transition: all 0.25s ease;
  box-shadow: 0 2px 6px rgba(29, 78, 255, 0.15);
  flex-shrink: 0;
}

:deep(.menu-name) {
  display: inline-flex; align-items: center; gap: 12px;
}

/* 展开列表头小图标 */
:deep(.expand-col-title) {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; color: var(--text-muted); font-size: 12px;
}
:deep(.menu-name-text) {
  font-weight: 600; color: var(--text-primary); font-size: 14px;
}
:deep(.menu-level-tag) {
  border-radius: 4px !important;
  font-size: 11px !important;
  padding: 0 8px !important;
  height: 20px;
  line-height: 18px;
}

:deep(.code-cell) {
  font-family: 'SF Mono', Consolas, Monaco, monospace;
  font-size: 12px;
  color: var(--text-secondary);
  background: #f1f5f9;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
}

:deep(.status-tag) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px !important;
  border-radius: 12px !important;
  font-size: 12px !important;
}
:deep(.status-dot) {
  width: 6px; height: 6px; border-radius: 50%;
  display: inline-block;
}
:deep(.status-dot.on) {
  background: #10b981;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
}
:deep(.status-dot.off) {
  background: #9ca3af;
}

/* ---------- 弹窗样式 ---------- */
.modal-header {
  display: flex; align-items: center; gap: 16px;
  padding: 4px 0 16px; border-bottom: 1px solid var(--border-color); margin-bottom: 16px;
}
.modal-header-icon {
  width: 48px; height: 48px; border-radius: 12px;
  background: linear-gradient(135deg, #f0f5ff 0%, #c7d8ff 100%);
  color: var(--primary-color);
  display: flex; align-items: center; justify-content: center; font-size: 22px;
}
.modal-header-title { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.modal-header-subtitle { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
.modal-footer {
  display: flex; justify-content: flex-end; gap: 12px;
  padding-top: 16px; margin-top: 16px; border-top: 1px solid var(--border-color);
}
.modal-footer .ant-btn-primary { min-width: 140px; }
</style>
