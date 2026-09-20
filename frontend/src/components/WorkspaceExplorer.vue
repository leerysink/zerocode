<script setup>
// 内置「类桌面」文件管理器（工作区浏览器）。
// 为什么需要它：后端跑在服务器 / 远端桌面上时，在服务器上调起 explorer 的窗口用户根本看不见，
// 于是「打开文件夹」降级成在网页里看目录 —— 判定与降级在 FilePanel，本组件只管展示。
// 数据源 GET /api/fs/ls（单层，不递归、不拉全树）；预览复用 GET /api/files/read（只读，
// 二进制与超 200KB 由后端拒读，这里展示原因并引导用下载）；下载走原生 <a href>。
// 本组件刻意不做增删改与上传：写文件仍走右栏 FilePanel 的编辑保存，权限面与原来一致。
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { FileOutlined, FolderOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { listDir, readFile } from '../api'
import { buildBreadcrumb, downloadUrl, formatBytes, formatMtime, parentPath } from '../utils/fsNav'

const props = defineProps({
  open: { type: Boolean, default: false },
  workspaceId: { type: Number, required: true },
  workspaceName: { type: String, default: '' },
})
const emit = defineEmits(['update:open'])

const path = ref('') // 当前所在层（工作区内相对 posix 路径，'' = 根）
const entries = ref([])
const loading = ref(false)
const previewPath = ref('')
const previewContent = ref('')
const previewError = ref('')

// 表格约定：columns 数组 + #bodyCell 渲染（名称/大小/时间/操作四列）
const columns = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: '大小', dataIndex: 'size', key: 'size', width: 96 },
  { title: '修改时间', dataIndex: 'mtime', key: 'mtime', width: 152 },
  { title: '操作', key: 'action', width: 72 },
]

const crumbs = computed(() => buildBreadcrumb(props.workspaceName, path.value))
const parent = computed(() => parentPath(path.value)) // null = 已在根层

async function load(target) {
  loading.value = true
  previewPath.value = ''
  previewContent.value = ''
  previewError.value = ''
  try {
    const { data } = await listDir(props.workspaceId, target)
    path.value = data.path
    entries.value = data.entries || []
  } catch (e) {
    message.error(e?.response?.data?.detail || '目录加载失败')
  } finally {
    loading.value = false
  }
}

// 只读预览：后端拒读（二进制/超 200KB）时把原因留在预览区，引导改用下载，不另弹错误
async function preview(record) {
  previewPath.value = record.path
  previewContent.value = ''
  previewError.value = ''
  try {
    previewContent.value = (await readFile(props.workspaceId, record.path)).data.content
  } catch (e) {
    previewError.value = e?.response?.data?.detail || '读取失败'
  }
}

// 命名注意：不能用 open —— <script setup> 的顶层绑定会盖掉同名 prop，模板里的 :open 就会拿到函数。
function activate(record) {
  if (record.is_dir) load(record.path)
  else preview(record)
}

// a-table 的 custom-row 会在每次渲染时对每条记录调用，返回值当行 DOM props 用。
// 所以它只能是"返回事件绑定"的工厂：在里面直接进目录/预览等于在渲染中改状态，会滚成无限更新环。
function rowProps(record) {
  return { onDblclick: () => activate(record) }
}

function close() {
  emit('update:open', false)
}

// 面包屑末项就是当前层，不可点；点其余项回到对应那一层
function go(crumb, index) {
  if (index !== crumbs.value.length - 1) load(crumb.path)
}

// 每次重新打开都回到工作区根，别把上次的层数留成"惊喜"
watch(() => props.open, (v) => {
  if (v) load('')
}, { immediate: true })
</script>

<template>
  <a-modal :open="open" title="工作区文件" width="70%" :footer="null" @cancel="close">
    <div class="zc-wex-bar">
      <a-button size="small" :disabled="parent === null" class="zc-wex-up" @click="parent !== null && load(parent)">上级</a-button>
      <a-button size="small" class="zc-wex-refresh" :loading="loading" @click="load(path)">
        <template #icon><reload-outlined /></template>
        刷新
      </a-button>
      <div class="zc-wex-crumbs">
        <template v-for="(c, i) in crumbs" :key="c.path">
          <span v-if="i" class="zc-wex-sep">/</span>
          <span
            :class="['zc-wex-crumb', i === 0 ? 'zc-wex-crumb-root' : '', i === crumbs.length - 1 ? 'zc-wex-crumb-current' : '']"
            :title="c.path || '工作区根'"
            @click="go(c, i)"
          >{{ c.label }}</span>
        </template>
      </div>
    </div>

    <a-spin v-if="loading" class="zc-wex-loading" />
    <a-empty v-else-if="!entries.length" description="空目录" />
    <a-table
      v-else
      :columns="columns"
      :data-source="entries"
      :pagination="false"
      size="small"
      row-key="path"
      :custom-row="rowProps"
      class="zc-wex-table"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <folder-outlined v-if="record.is_dir" class="zc-wex-icon zc-wex-icon-dir" />
          <file-outlined v-else class="zc-wex-icon" />
          <span class="zc-wex-name" :title="record.path">{{ record.name }}</span>
        </template>
        <span v-else-if="column.key === 'size'">{{ formatBytes(record.size, record.is_dir) }}</span>
        <span v-else-if="column.key === 'mtime'">{{ formatMtime(record.mtime) }}</span>
        <a
          v-else-if="column.key === 'action' && !record.is_dir"
          class="zc-wex-download"
          :href="downloadUrl(workspaceId, record.path)"
        >下载</a>
        <span v-else-if="column.key === 'action'" class="zc-wex-na">—</span>
      </template>
    </a-table>

    <div v-if="previewError" class="zc-wex-error">{{ previewError }}</div>
    <pre v-else-if="previewPath" class="zc-wex-preview">{{ previewContent }}</pre>
    <p v-else class="zc-wex-hint">双击目录进入，双击文件预览；文件可直接下载（二进制与大文件走下载）。</p>
  </a-modal>
</template>

<style scoped>
/* 素雅低饱和：沿用全局 CSS 变量，不自带配色 */
.zc-wex-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.zc-wex-crumbs {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--zc-text-sub);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.zc-wex-crumb {
  cursor: pointer;
}
.zc-wex-crumb:hover {
  color: var(--zc-text);
}
.zc-wex-crumb-current {
  cursor: default;
  color: var(--zc-text);
}
.zc-wex-sep {
  color: var(--zc-border);
}
.zc-wex-loading {
  display: block;
  margin: 24px auto;
}
.zc-wex-icon {
  margin-right: 6px;
  color: var(--zc-text-sub);
}
.zc-wex-icon-dir {
  color: #b0895a; /* 低饱和的木色，只给目录用，图标与名称仍能一眼分开 */
}
.zc-wex-name {
  word-break: break-all;
}
.zc-wex-na {
  color: var(--zc-border);
}
.zc-wex-preview {
  margin: 8px 0 0;
  padding: 8px;
  max-height: 34vh;
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
.zc-wex-error {
  margin: 8px 0 0;
  padding: 8px;
  border: 1px solid var(--zc-border);
  border-radius: 4px;
  background: var(--zc-bg);
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-wex-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--zc-text-sub);
}
</style>
