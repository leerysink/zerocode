<script setup>
// 内置「类桌面」文件管理器（工作区浏览器）：左树右预览，只读。
// 为什么需要它：后端跑在服务器 / 远端桌面上时，在服务器上调起 explorer 的窗口用户根本看不见，
// 于是「打开文件夹」降级成在网页里看目录 —— 判定与降级在调用方（FilePanel / AppsView），本组件只管展示。
// 数据源 GET /api/fs/ls 懒加载：展开一层才拉一层，体积随可见范围走，不一次拉全树（ADR-0010）。
// 只读：看目录与下载在此，编辑仍走聊天页右栏的文件面板，权限面与改造前一致。
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { listDir, readFile } from '../api'
import {
  ancestorsOf, attachChildren, buildBreadcrumb, deepestPath, downloadUrl, findNode, formatBytes, formatMtime, toTreeNodes,
} from '../utils/fsNav'

const props = defineProps({
  open: { type: Boolean, default: false },
  workspaceId: { type: Number, required: true },
  workspaceName: { type: String, default: '' },
})
const emit = defineEmits(['update:open'])

const treeData = ref([])
const expandedKeys = ref([])
const loadedKeys = ref([]) // 受控回写：不重置它，切换工作区后 ant 会拿旧缓存认为目录已加载
const selectedKeys = ref([])
const focusPath = ref('') // 当前在哪一层：目录不进右栏，层级感全靠面包屑
const rootLoading = ref(false)
const previewPath = ref('')
const previewContent = ref('')
const previewError = ref('')
const previewLoading = ref(false)
const previewNode = computed(() => findNode(treeData.value, previewPath.value))

const crumbs = computed(() => buildBreadcrumb(props.workspaceName, focusPath.value))

// 世代号：每次重置（关弹框重开 / 切工作区 / 点刷新）自增。
// 在途请求回来时先比世代，不匹配就丢弃 —— 否则关掉再打开后，上一轮的迟到响应会
// 把旧工作区的子节点挂进新树、还把那一层标成“已加载”，用户再也拉不动它。
let gen = 0

function reset() {
  gen += 1
  treeData.value = []
  expandedKeys.value = []
  loadedKeys.value = []
  selectedKeys.value = []
  focusPath.value = ''
  clearPreview()
}

function clearPreview() {
  previewPath.value = ''
  previewContent.value = ''
  previewError.value = ''
  // 重置时预览还可能在途：迟到的响应走过期分支不会再替它收尾，加载态只能在这里落清
  previewLoading.value = false
}

async function loadRoot() {
  reset()
  const g = gen
  rootLoading.value = true
  try {
    const { data } = await listDir(props.workspaceId, '')
    if (g !== gen) return
    treeData.value = toTreeNodes(data.entries)
  } catch (e) {
    if (g !== gen) return
    message.error(e?.response?.data?.detail || '目录加载失败')
  } finally {
    if (g === gen) rootLoading.value = false
  }
}

// loadData 必须返回 Promise：ant 靠它的 resolve/reject 决定要不要把 key 记进 loadedKeys。
// 失败要在提示之后继续 throw，让这一层别被标记成"已加载"，用户才收得起、也重试得了
// （ant 会兜住这个 rejection，但展开恢复链里还会再冒一个未处理异常，那是 ant 自身行为）。
async function loadNode(node) {
  // ant 的 expandAction='click' 对叶子同样会 toggle 并调 loadData（不判断 isLeaf），
  // 文件不进懒加载：否则点一下文件就拿文件路径去打 /fs/ls（后端按目录校验，只会 4xx）。
  if (!findNode(treeData.value, node.key)?.dir) return
  const g = gen
  try {
    const { data } = await listDir(props.workspaceId, node.key)
    if (g !== gen) return
    treeData.value = attachChildren(treeData.value, node.key, data.entries)
    // 受控 loadedKeys 得组件自己回写；并发展开两层时覆盖式赋值会丢掉其中一层的 key，所以按 Set 合并
    loadedKeys.value = [...new Set([...loadedKeys.value, node.key])]
  } catch (e) {
    if (g !== gen) return
    message.error(e?.response?.data?.detail || '目录加载失败')
    expandedKeys.value = expandedKeys.value.filter((k) => k !== node.key)
    focusPath.value = deepestPath(expandedKeys.value) // 面包屑跟着退回还看得见的那一层，别停在失败层
    throw e
  }
}

function onExpand(keys, { node, expanded }) {
  // 点文件行时 ant 也会把文件 key 塞进 keys（同上：不判断叶子），只有目录配进展开态，
  // 否则面包屑与懒加载都会被一个永远不会有子节点的文件带歪。
  const dirKeys = keys.filter((k) => findNode(treeData.value, k)?.dir)
  expandedKeys.value = dirKeys
  const isDir = findNode(treeData.value, node.key)?.dir
  focusPath.value = expanded && isDir ? node.key : deepestPath(dirKeys)
}

// 只读预览：后端拒读（二进制/超 200KB）时把原因留在预览区，跟着下载链接一起给出，不另弹错误。
// 点目录不走这里：目录只展开，右栏永远是一个具体文件。
async function onSelect(keys) {
  const key = keys[0]
  if (!key) return // 再点一次已选中的节点 = ant 的取消选中，预览保持原样不闪空
  const node = findNode(treeData.value, key)
  if (!node || node.dir) return
  const g = gen
  selectedKeys.value = [key]
  const chain = ancestorsOf(key)
  focusPath.value = chain.length ? chain[chain.length - 1] : '' // 面包屑走到文件所在那一层
  previewPath.value = key
  previewContent.value = ''
  previewError.value = ''
  previewLoading.value = true
  // 连点两个文件时，慢的那个后回来不能把已经显示的快响应盖掉 —— 当前预览目标换了就当过期
  const stale = () => g !== gen || previewPath.value !== key
  try {
    const content = (await readFile(props.workspaceId, key)).data.content
    if (stale()) return
    previewContent.value = content
  } catch (e) {
    if (stale()) return
    previewError.value = e?.response?.data?.detail || '读取失败'
  } finally {
    if (!stale()) previewLoading.value = false
  }
}

function close() {
  emit('update:open', false)
}

// 点面包屑 = 折回那一层：路径早已在树里（可见层才进面包屑），只调展开态，不再发请求
function go(crumb, index) {
  if (index === crumbs.value.length - 1) return
  focusPath.value = crumb.path
  expandedKeys.value = crumb.path ? ancestorsOf(crumb.path).concat(crumb.path) : []
}

// 每次重新打开、以及换工作区，都从工作区根重来，别把上次的展开态留成"惊喜"。
// 合成一个 watch：open 与 workspaceId 同帧变化时（例如从另一个工作区直接开弹框）只拉一次根层，
// 分成两个 watch 会各触发一次 loadRoot，同一层连发两个请求、慢的那个还可能盖掉快的。
watch([() => props.open, () => props.workspaceId], ([open]) => {
  if (open) loadRoot()
}, { immediate: true })
</script>

<template>
  <a-modal :open="open" title="工作区文件" width="90%" :footer="null" @cancel="close">
    <div class="zc-wex-bar">
      <a-button size="small" class="zc-wex-refresh" :loading="rootLoading" @click="loadRoot">
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

    <div class="zc-wex-body">
      <div class="zc-wex-side zc-wex-tree-side">
        <a-spin v-if="rootLoading" class="zc-wex-loading" />
        <a-empty v-else-if="!treeData.length" description="空目录" />
        <a-tree
          v-else
          :tree-data="treeData"
          :expanded-keys="expandedKeys"
          :loaded-keys="loadedKeys"
          :selected-keys="selectedKeys"
          :load-data="loadNode"
          block-node
          expand-action="click"
          @expand="onExpand"
          @select="onSelect"
        />
      </div>
      <div class="zc-wex-side zc-wex-preview-side">
        <template v-if="previewPath">
          <div class="zc-wex-meta">
            <span class="zc-wex-meta-path" :title="previewPath">{{ previewPath }}</span>
            <span>{{ formatBytes(previewNode?.size, false) }}</span>
            <span>{{ formatMtime(previewNode?.mtime) }}</span>
            <a class="zc-wex-download" :href="downloadUrl(workspaceId, previewPath)">下载</a>
          </div>
          <a-spin v-if="previewLoading" class="zc-wex-loading" />
          <div v-else-if="previewError" class="zc-wex-error">{{ previewError }}</div>
          <pre v-else class="zc-wex-preview">{{ previewContent }}</pre>
        </template>
        <p v-else class="zc-wex-hint">展开目录看下一级（点目录只展开，不占右侧），点文件在这里预览内容；二进制与大文件走下载。</p>
      </div>
    </div>
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
/* 左右两栏各自定高内部滚动：树再深、文件再长都不会把弹框顶成一屏瀑布 */
.zc-wex-body {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.zc-wex-side {
  height: 58vh;
  overflow: auto;
  box-sizing: border-box;
}
.zc-wex-tree-side {
  flex: 0 0 42%;
  min-width: 0;
  padding-right: 8px;
  border-right: 1px solid var(--zc-border);
}
.zc-wex-preview-side {
  flex: 1;
  min-width: 0;
}
.zc-wex-loading {
  display: block;
  margin: 24px auto;
}
.zc-wex-hint {
  margin: 0;
  font-size: 12px;
  color: var(--zc-text-sub);
}
/* 属性行：路径 + 大小 + 修改时间 + 下载，跟桌面文件管理器的信息栏同序 */
.zc-wex-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-wex-meta-path {
  min-width: 0;
  max-width: 60%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--zc-text);
}
.zc-wex-download {
  margin-left: auto;
  flex: none;
}
.zc-wex-preview {
  margin: 0;
  padding: 8px;
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
  margin: 0;
  padding: 8px;
  border: 1px solid var(--zc-border);
  border-radius: 4px;
  background: var(--zc-bg);
  font-size: 12px;
  color: var(--zc-text-sub);
}
</style>
