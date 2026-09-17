<script setup>
// 右栏文件面板（Task 14）：可折叠 a-layout-sider（宽 300 / collapsedWidth 0）。
// 「文件」tab：工作区文件树（GET /api/fs/tree → treeData）+ 顶部刷新按钮；
//   点击文件 → 内容预览（pre）；后端对二进制/超 200KB 文件拒读（400），错误详情展示在预览区即「>200KB 提示」；
//   「编辑」切 textarea + 保存（PUT /api/files/save），保存后刷新文件树（可能新增文件）。
// 「变更记录」tab：GET /api/sessions/{id}/file-changes（id 倒序），逐条 DiffBlock 显示 path+action+diff。
import { onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { getFileChanges, readFile, saveFile, tree } from '../api'
import { useChatStore } from '../stores/chat'
import DiffBlock from './DiffBlock.vue'

const props = defineProps({
  workspaceId: { type: Number, required: true },
})

const store = useChatStore()
const tab = ref('files')
const treeData = ref([])
const treeLoading = ref(false)
const expandedKeys = ref([])

// 预览状态：previewError 非空表示读失败（二进制/超 200KB/越界等）
const selectedPath = ref('')
const content = ref('')
const previewError = ref('')
const loading = ref(false)
const editing = ref(false)
const saving = ref(false)

// 变更记录
const changes = ref([])
const changesLoading = ref(false)

// /fs/tree 的 {name,path,type,children} → a-tree 的 {title,key,isLeaf,children}
// 顺带建 key→节点 映射：a-tree select 事件参数里取自定义字段不可靠，用映射判定叶子
const nodeMap = {}
function toTreeData(nodes) {
  return (nodes || []).map((n) => {
    nodeMap[n.path] = n
    return {
      title: n.name,
      key: n.path,
      isLeaf: n.type !== 'dir',
      children: n.children ? toTreeData(n.children) : undefined,
    }
  })
}

async function loadTree() {
  treeLoading.value = true
  try {
    Object.keys(nodeMap).forEach((k) => delete nodeMap[k]) // 重拉前清映射，防跨工作区/重载后的陈旧条目
    treeData.value = toTreeData((await tree(props.workspaceId)).data)
  } catch (e) {
    message.error('文件树加载失败')
  } finally {
    treeLoading.value = false
  }
}

// 仅文件叶子可选；读取失败（二进制/超 200KB）在预览区展示后端 detail
async function onSelect(keys) {
  const path = keys[0]
  if (!path || nodeMap[path]?.type === 'dir') return
  selectedPath.value = keys[0]
  editing.value = false
  previewError.value = ''
  content.value = ''
  loading.value = true
  try {
    content.value = (await readFile(props.workspaceId, keys[0])).data.content
  } catch (e) {
    previewError.value = e?.response?.data?.detail || '读取失败'
  } finally {
    loading.value = false
  }
}

function cancelEdit() {
  editing.value = false
  // 取消编辑：回退到磁盘内容（读取失败场景编辑入口本就不出现，此处 content 必为已加载内容）
  loading.value = true
  readFile(props.workspaceId, selectedPath.value)
    .then(({ data }) => (content.value = data.content))
    .catch((e) => (previewError.value = e?.response?.data?.detail || '读取失败'))
    .finally(() => (loading.value = false))
}

async function persist() {
  saving.value = true
  try {
    await saveFile({ workspace_id: props.workspaceId, path: selectedPath.value, content: content.value })
    message.success('已保存')
    editing.value = false
    await loadTree()
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function loadChanges() {
  if (!store.currentSessionId) return
  changesLoading.value = true
  try {
    changes.value = (await getFileChanges(store.currentSessionId)).data
  } catch (e) {
    message.error('变更记录加载失败')
  } finally {
    changesLoading.value = false
  }
}

// 切到「变更记录」tab 时按当前会话拉取（a-tab-click 回传 key）
function onTabClick(key) {
  if (key === 'changes') loadChanges()
}

// 工作区切换：清空预览状态并重拉文件树
watch(() => props.workspaceId, () => {
  selectedPath.value = ''
  content.value = ''
  previewError.value = ''
  editing.value = false
  loadTree()
})

onMounted(loadTree)
</script>

<template>
  <a-layout-sider class="zc-fp" :width="300" :collapsed-width="0" collapsible theme="light">
    <a-tabs v-model:activeKey="tab" size="middle" class="zc-fp-tabs" @tab-click="onTabClick">
      <a-tab-pane key="files" tab="文件">
        <div class="zc-fp-toolbar">
          <a-button size="small" :loading="treeLoading" @click="loadTree">
            <template #icon><reload-outlined /></template>
            刷新
          </a-button>
        </div>
        <a-spin v-if="treeLoading" class="zc-fp-loading" />
        <a-empty v-else-if="!treeData.length" description="工作区为空" />
        <a-tree
          v-else
          :tree-data="treeData"
          v-model:expanded-keys="expandedKeys"
          block-node
          @select="onSelect"
        />
        <div v-if="selectedPath" class="zc-fp-preview">
          <div class="zc-fp-preview-head">
            <span class="zc-fp-file" :title="selectedPath">{{ selectedPath }}</span>
            <a-button v-if="!editing && !previewError" size="small" @click="editing = true">编辑</a-button>
          </div>
          <a-spin v-if="loading" class="zc-fp-loading" />
          <template v-else>
            <a-alert v-if="previewError" type="warning" :message="previewError" show-icon />
            <template v-else-if="editing">
              <a-textarea v-model:value="content" :rows="12" class="zc-fp-editor" />
              <div class="zc-fp-actions">
                <a-button size="small" type="primary" :loading="saving" @click="persist">保存</a-button>
                <a-button size="small" @click="cancelEdit">取消</a-button>
              </div>
            </template>
            <pre v-else class="zc-fp-content">{{ content }}</pre>
          </template>
        </div>
      </a-tab-pane>
      <a-tab-pane key="changes" tab="变更记录">
        <div class="zc-fp-toolbar">
          <a-button size="small" :loading="changesLoading" @click="loadChanges">
            <template #icon><reload-outlined /></template>
            刷新
          </a-button>
        </div>
        <a-empty v-if="!store.currentSessionId" description="打开会话后可查看变更记录" />
        <a-spin v-else-if="changesLoading" class="zc-fp-loading" />
        <a-empty v-else-if="!changes.length" description="暂无变更" />
        <div v-else class="zc-fp-changes">
          <DiffBlock v-for="c in changes" :key="c.id" :diff="c.diff" :path="c.path" :action="c.action" />
        </div>
      </a-tab-pane>
    </a-tabs>
  </a-layout-sider>
</template>

<style scoped>
.zc-fp {
  background: var(--zc-sidebar-bg) !important;
  border-left: 1px solid var(--zc-border);
  overflow-y: auto;
}
.zc-fp-tabs {
  padding: 0 10px;
}
.zc-fp-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 8px;
}
.zc-fp-loading {
  display: block;
  margin: 24px auto;
}
.zc-fp-preview {
  border-top: 1px solid var(--zc-border);
  padding-top: 8px;
  margin-top: 4px;
}
.zc-fp-preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.zc-fp-file {
  font-size: 12px;
  color: var(--zc-text-sub);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.zc-fp-content {
  margin: 0;
  padding: 8px;
  max-height: 40vh;
  overflow: auto;
  background: var(--zc-bg);
  border: 1px solid var(--zc-border);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--zc-text);
}
.zc-fp-editor {
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
}
.zc-fp-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.zc-fp-changes {
  padding-bottom: 8px;
}
</style>
