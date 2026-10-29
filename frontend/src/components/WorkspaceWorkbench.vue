<script setup>
// 工作区工作台（ADR-0022）：左边懒加载文件树，顶部页签，右侧编辑区，底部状态栏。
// 树沿用 WorkspaceExplorer 的实现纪律：世代号丢弃迟到响应、受控 loadedKeys 回写、展开失败不记缓存。
// 页签与文件缓冲都按「路径」收口：同一路径只有一个页签；关闭激活页签取右邻，没有右邻取左邻。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { listDir, readFile, saveFile } from '../api'
import {
  ancestorsOf, attachChildren, buildBreadcrumb, deepestPath, findNode, toTreeNodes,
} from '../utils/fsNav'
import {
  closeTab, decodeTabs, encodeTabs, openTab, tabsStorageKey,
} from '../utils/workbenchTabs'
import { isReadOnlyPath } from '../utils/fileTypes'
import { nextTheme, readWorkbenchTheme, writeWorkbenchTheme } from '../utils/theme'
import FileIcon from './workbench/FileIcon.vue'
import WorkbenchEditor from './workbench/WorkbenchEditor.vue'
import WorkbenchStatusBar from './workbench/WorkbenchStatusBar.vue'

const props = defineProps({
  // 直链地址带进来的是字符串，入口点过来的是数字：两种都收，原样透传不转 Number
  workspaceId: { type: [Number, String], required: true },
  workspaceName: { type: String, default: '' },
  initialFile: { type: String, default: '' },
})

const treeData = ref([])
const expandedKeys = ref([])
const loadedKeys = ref([])
const selectedKeys = ref([])
const focusPath = ref('')
const rootLoading = ref(false)

const tabs = ref([])
const active = ref('')
const files = ref({})
const cursor = ref({ line: 1, column: 1, selected: 0 })

// 主题只落在工作台根节点上（data-theme + 局部 CSS 变量），不动全局 :root
const theme = ref(readWorkbenchTheme())

function toggleTheme() {
  theme.value = nextTheme(theme.value)
  writeWorkbenchTheme(theme.value)
}

// 状态栏的保存态与冲突/关闭确认弹框；saving 收口并发保存（一次只发一个写请求）
const saveText = ref('')
const closePrompt = ref('')
const conflict = ref(null) // { path, content, mtimeNs }：409 时待用户选择覆盖/重新加载/取消
const saving = ref(false)
let savedTimer = null

const crumbs = computed(() => buildBreadcrumb(props.workspaceName, focusPath.value))
const activeEntry = computed(() => files.value[active.value] || null)
const openPaths = computed(() => tabs.value.map((t) => t.path))

// 世代号：刷新/换工作区自增；在途请求回来先比世代，避免旧响应挂进新树或新页签
let gen = 0

function resetTree() {
  gen += 1
  treeData.value = []
  expandedKeys.value = []
  loadedKeys.value = []
  selectedKeys.value = []
  focusPath.value = ''
}

function resetAll() {
  resetTree()
  tabs.value = []
  active.value = ''
  files.value = {}
}

// 页签记忆：只存路径与激活项，不存草稿。坏值（坏 JSON / 非数组 / 非字符串项）整条移除，
// 不让一份坏存储把工作台卡死在「恢复不出来」和「每次再来一遍」之间。
function readStoredTabs() {
  const key = tabsStorageKey(props.workspaceId)
  let raw = null
  try {
    raw = localStorage.getItem(key)
  } catch {
    return { tabs: [], active: '' }
  }
  if (raw == null) return { tabs: [], active: '' }
  let valid = false
  try {
    const data = JSON.parse(raw)
    valid = !!data && typeof data === 'object' && !Array.isArray(data) && Array.isArray(data.tabs)
      && data.tabs.every((p) => typeof p === 'string' && p)
  } catch {
    valid = false
  }
  if (!valid) {
    try {
      localStorage.removeItem(key)
    } catch {
      /* localStorage 不可用时按空列表处理 */
    }
    return { tabs: [], active: '' }
  }
  return decodeTabs(raw)
}

let storageWarned = false
// 与存储里的值一致就不再写：挂载时的 resetAll 不该把「坏值已删」的空状态又写回去
let lastPersisted = encodeTabs([], '')

function persistTabs() {
  const encoded = encodeTabs(tabs.value, active.value)
  if (encoded === lastPersisted) return
  try {
    localStorage.setItem(tabsStorageKey(props.workspaceId), encoded)
    lastPersisted = encoded
  } catch {
    if (storageWarned) return
    storageWarned = true
    message.warning('页签无法记忆')
  }
}

async function restoreTabs() {
  const saved = readStoredTabs()
  if (saved.tabs.length) tabs.value = saved.tabs
  if (saved.active) await selectTab(saved.active)
  else active.value = ''
}

watch([tabs, active], persistTabs)

function onBeforeUnload(e) {
  const dirtyAny = tabs.value.some((t) => files.value[t.path]?.dirty)
  if (!dirtyAny) return
  e.preventDefault()
  e.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
})

async function loadRoot() {
  resetTree()
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

async function loadNode(node) {
  if (!findNode(treeData.value, node.key)?.dir) return
  const g = gen
  try {
    const { data } = await listDir(props.workspaceId, node.key)
    if (g !== gen) return
    treeData.value = attachChildren(treeData.value, node.key, data.entries)
    loadedKeys.value = [...new Set([...loadedKeys.value, node.key])]
  } catch (e) {
    if (g !== gen) return
    message.error(e?.response?.data?.detail || '目录加载失败')
    expandedKeys.value = expandedKeys.value.filter((k) => k !== node.key)
    focusPath.value = deepestPath(expandedKeys.value)
    throw e
  }
}

function onExpand(keys, { node, expanded }) {
  const dirKeys = keys.filter((k) => findNode(treeData.value, k)?.dir)
  expandedKeys.value = dirKeys
  const isDir = findNode(treeData.value, node.key)?.dir
  focusPath.value = expanded && isDir ? node.key : deepestPath(dirKeys)
}

function setFile(path, patch) {
  files.value = { ...files.value, [path]: { ...(files.value[path] || {}), ...patch } }
}

function dropFile(path) {
  if (!files.value[path]) return
  const next = { ...files.value }
  delete next[path]
  files.value = next
}

function isCurrent(path, g) {
  return g === gen && tabs.value.some((t) => t.path === path)
}

async function ensureLoaded(path) {
  const cur = files.value[path]
  if (cur && (cur.loading || cur.loaded || cur.error)) return
  const g = gen
  setFile(path, {
    content: '',
    savedContent: '',
    mtimeNs: null,
    loading: true,
    loaded: false,
    error: '',
    readOnly: isReadOnlyPath(path),
    dirty: false,
  })
  try {
    const { data } = await readFile(props.workspaceId, path)
    if (!isCurrent(path, g)) return
    const content = String(data?.content ?? '')
    setFile(path, {
      content,
      savedContent: content,
      mtimeNs: data?.mtime_ns ?? null,
      loading: false,
      loaded: true,
      error: '',
      readOnly: isReadOnlyPath(path),
      dirty: false,
    })
  } catch (e) {
    if (!isCurrent(path, g)) return
    setFile(path, {
      content: '',
      savedContent: '',
      mtimeNs: null,
      loading: false,
      loaded: false,
      error: e?.response?.data?.detail || '读取失败',
      readOnly: isReadOnlyPath(path),
      dirty: false,
    })
  }
}

async function activateTab(path) {
  if (!path) return
  active.value = path
  selectedKeys.value = [path]
  const chain = ancestorsOf(path)
  focusPath.value = chain.length ? chain[chain.length - 1] : ''
  await ensureLoaded(path)
}

// 激活页签同时把树揭示到该文件：切到深层页签时逐级展开，右栏跳转/内部链接也复用同一条路径
async function selectTab(path) {
  await activateTab(path)
  await expandTo(path)
}

async function openFile(path) {
  const r = openTab(tabs.value, path)
  tabs.value = r.tabs
  await selectTab(path)
}

function onSelect(keys) {
  const key = keys[0]
  if (!key) return
  const node = findNode(treeData.value, key)
  if (!node || node.dir) return
  openFile(key)
}

function onContent(value) {
  const path = active.value
  if (!path) return
  setFile(path, { content: value })
}

// 切页签后先回到 1,1，等 Monaco 把该文件恢复出的光标位置报上来再覆盖
watch(active, () => {
  cursor.value = { line: 1, column: 1, selected: 0 }
})

function onCursor(c) {
  cursor.value = {
    line: Number(c?.line) || 1,
    column: Number(c?.column) || 1,
    selected: Number(c?.selected) || 0,
  }
}

function onDirty(dirty) {
  const path = active.value
  if (!path) return
  setFile(path, { dirty: !!dirty })
  if (dirty) clearSavedText()
}

function clearSavedText() {
  if (savedTimer) {
    clearTimeout(savedTimer)
    savedTimer = null
  }
  if (saveText.value === '已保存') saveText.value = ''
}

function flashSaved() {
  if (savedTimer) clearTimeout(savedTimer)
  saveText.value = '已保存'
  savedTimer = setTimeout(() => {
    savedTimer = null
    if (saveText.value === '已保存') saveText.value = ''
  }, 2000)
}

// 保存激活页签：请求带打开/上次保存时的 mtime；响应只认发起时的快照——
// 期间继续输入就不清脏，版本戳也只前进到后端刚写入的那个值。
async function saveActive() {
  const path = active.value
  if (!path || saving.value || conflict.value) return
  const entry = files.value[path]
  if (!entry || !entry.loaded || entry.loading || entry.error || entry.readOnly || !entry.dirty) return
  const content = entry.content
  const payload = { workspace_id: props.workspaceId, path, content }
  if (entry.mtimeNs != null) payload.base_mtime_ns = entry.mtimeNs
  saving.value = true
  saveText.value = '保存中…'
  try {
    const { data } = await saveFile(payload)
    const cur = files.value[path]
    if (cur) {
      setFile(path, {
        mtimeNs: data?.mtime_ns ?? cur.mtimeNs,
        savedContent: content,
        dirty: cur.content === content ? false : cur.dirty,
      })
      if (cur.content === content) flashSaved()
    }
  } catch (e) {
    if (e?.response?.status === 409) {
      // 磁盘被外部改过：不静默覆盖，交给用户三选一
      conflict.value = { path, content, mtimeNs: e.response.data?.mtime_ns ?? null }
      saveText.value = ''
    } else {
      clearSavedText()
      message.error(e?.response?.data?.detail || '保存失败')
    }
  } finally {
    saving.value = false
    if (saveText.value === '保存中…') saveText.value = ''
  }
}

// 覆盖 = 拿 409 带回的当前 mtime 当基准重发；期间又被改就再弹一次，绝不做无校验强写
async function overwriteConflict() {
  const c = conflict.value
  if (!c || saving.value) return
  saving.value = true
  saveText.value = '保存中…'
  const payload = { workspace_id: props.workspaceId, path: c.path, content: c.content }
  if (c.mtimeNs != null) payload.base_mtime_ns = c.mtimeNs
  try {
    const { data } = await saveFile(payload)
    const cur = files.value[c.path]
    if (cur) {
      const unchanged = cur.content === c.content
      setFile(c.path, {
        mtimeNs: data?.mtime_ns ?? cur.mtimeNs,
        savedContent: c.content,
        dirty: unchanged ? false : cur.dirty,
      })
      if (unchanged) flashSaved()
    }
    conflict.value = null
  } catch (e) {
    if (e?.response?.status === 409) {
      conflict.value = { ...c, mtimeNs: e.response.data?.mtime_ns ?? null }
      saveText.value = ''
    } else {
      conflict.value = null
      clearSavedText()
      message.error(e?.response?.data?.detail || '保存失败')
    }
  } finally {
    saving.value = false
    if (saveText.value === '保存中…') saveText.value = ''
  }
}

// 重新加载 = 丢弃本地改动，重新读盘（内容、mtime 一起换新，脏态归零）
function reloadConflict() {
  const c = conflict.value
  if (!c) return
  conflict.value = null
  saveText.value = ''
  dropFile(c.path)
  ensureLoaded(c.path)
}

function cancelConflict() {
  conflict.value = null
}

function doClose(path) {
  const wasActive = active.value === path
  const r = closeTab(tabs.value, path, active.value)
  tabs.value = r.tabs
  dropFile(path)
  if (!wasActive) return
  if (r.active) selectTab(r.active)
  else {
    active.value = ''
    selectedKeys.value = []
  }
}

// 关闭脏页签先问一句：确认丢弃才真的关，取消则内容与脏态原样保留
function requestClose(path) {
  if (files.value[path]?.dirty) {
    closePrompt.value = path
    return
  }
  doClose(path)
}

function confirmDiscard() {
  const path = closePrompt.value
  closePrompt.value = ''
  if (path) doClose(path)
}

function cancelDiscard() {
  closePrompt.value = ''
}

function go(crumb, index) {
  if (index === crumbs.value.length - 1) return
  focusPath.value = crumb.path
  expandedKeys.value = crumb.path ? ancestorsOf(crumb.path).concat(crumb.path) : []
}

async function revealFile(path) {
  if (!path) return
  await openFile(path)
}

// 从根往下逐级拉祖先目录，把文件所在目录展开出来；哪一层缺失就停下，不弹错
// （文件本身读不到由页签展示错误，树这边保持安静）。
async function expandTo(path) {
  const g = gen
  const dirs = String(path).split('/').filter(Boolean).slice(0, -1)
  const loaded = []
  let prefix = ''
  try {
    for (const seg of dirs) {
      const dir = prefix ? `${prefix}/${seg}` : seg
      const node = findNode(treeData.value, dir)
      if (!node || !node.dir) break
      if (!loadedKeys.value.includes(dir)) {
        const { data } = await listDir(props.workspaceId, dir)
        if (g !== gen) return
        treeData.value = attachChildren(treeData.value, dir, data.entries)
        loadedKeys.value = [...new Set([...loadedKeys.value, dir])]
      }
      loaded.push(dir)
      prefix = dir
    }
  } catch {
    if (g !== gen) return
  }
  if (g !== gen) return
  if (loaded.length) {
    expandedKeys.value = [...new Set([...expandedKeys.value, ...loaded])]
    focusPath.value = loaded[loaded.length - 1]
  }
}

const baseName = (path) => String(path).split('/').pop() || String(path)

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
  if (savedTimer) clearTimeout(savedTimer)
})

watch(() => props.workspaceId, async () => {
  resetAll()
  saveText.value = ''
  closePrompt.value = ''
  conflict.value = null
  lastPersisted = encodeTabs([], '')
  const root = loadRoot()
  const g = gen
  await root
  if (g !== gen) return
  await restoreTabs()
  if (g !== gen) return
  if (props.initialFile) await revealFile(props.initialFile)
}, { immediate: true })
</script>

<template>
  <div class="zc-wb" :data-theme="theme">
    <aside class="zc-wb-side">
      <div class="zc-wb-side-head">
        <span class="zc-wb-ws-name" :title="workspaceName || '工作区'">{{ workspaceName || '工作区' }}</span>
        <a-button size="small" class="zc-wb-refresh" :loading="rootLoading" @click="loadRoot">
          <template #icon><reload-outlined /></template>
          刷新
        </a-button>
      </div>

      <div class="zc-wb-crumbs">
        <template v-for="(c, i) in crumbs" :key="c.path">
          <span v-if="i" class="zc-wb-sep">/</span>
          <span
            :class="['zc-wb-crumb', i === 0 ? 'zc-wb-crumb-root' : '', i === crumbs.length - 1 ? 'zc-wb-crumb-current' : '']"
            :title="c.path || '工作区根'"
            @click="go(c, i)"
          >{{ c.label }}</span>
        </template>
      </div>

      <div class="zc-wb-tree">
        <a-spin v-if="rootLoading" class="zc-wb-loading" />
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
        >
          <template #title="node">
            <span class="zc-wb-node">
              <FileIcon :path="node.key" :dir="!!node.dir" />
              <span class="zc-wb-node-name">{{ node.title }}</span>
            </span>
          </template>
        </a-tree>
      </div>
    </aside>

    <section class="zc-wb-main">
      <div v-if="tabs.length" class="zc-wb-tabs">
        <div
          v-for="t in tabs"
          :key="t.path"
          :class="['zc-wb-tab', t.path === active ? 'is-active' : '']"
          :title="t.path"
          @click="selectTab(t.path)"
        >
          <FileIcon class="zc-wb-tab-icon" :path="t.path" />
          <span class="zc-wb-tab-title">{{ baseName(t.path) }}</span>
          <span v-if="files[t.path]?.dirty" class="zc-wb-dirty" aria-label="未保存" />
          <button class="zc-wb-tab-close" type="button" aria-label="关闭" @click.stop="requestClose(t.path)">×</button>
        </div>
      </div>

      <WorkbenchEditor
        :workspace-id="workspaceId"
        :path="active"
        :content="activeEntry?.content ?? ''"
        :saved-content="activeEntry?.savedContent ?? ''"
        :loading="!!activeEntry?.loading"
        :error="activeEntry?.error || ''"
        :read-only="!!activeEntry?.readOnly"
        :theme="theme"
        :open-paths="openPaths"
        @update:content="onContent"
        @update:dirty="onDirty"
        @cursor="onCursor"
        @save="saveActive"
        @open-file="openFile"
      />

      <WorkbenchStatusBar
        :path="active"
        :content="activeEntry?.content ?? ''"
        :cursor="cursor"
        :read-only="!!activeEntry?.readOnly"
        :save-text="saveText"
        :theme="theme"
        @toggle-theme="toggleTheme"
      />
    </section>

    <a-modal
      :open="!!closePrompt"
      title="未保存的改动"
      class="zc-wb-close-modal"
      @cancel="cancelDiscard"
    >
      <p class="zc-wb-close-text">「{{ baseName(closePrompt) }}」有未保存的改动，关闭后会丢弃这些改动。</p>
      <template #footer>
        <a-button class="zc-wb-close-cancel" @click="cancelDiscard">取消</a-button>
        <a-button danger class="zc-wb-close-discard" @click="confirmDiscard">关闭并丢弃</a-button>
      </template>
    </a-modal>

    <a-modal
      :open="!!conflict"
      title="保存冲突"
      class="zc-wb-conflict"
      @cancel="cancelConflict"
    >
      <p class="zc-wb-conflict-text">磁盘上的文件已被其他改动。继续保存会覆盖磁盘上的内容，也可以重新加载磁盘上的版本。</p>
      <template #footer>
        <a-button class="zc-wb-conflict-cancel" @click="cancelConflict">取消</a-button>
        <a-button class="zc-wb-conflict-reload" @click="reloadConflict">重新加载</a-button>
        <a-button danger class="zc-wb-conflict-overwrite" @click="overwriteConflict">覆盖</a-button>
      </template>
    </a-modal>
  </div>
</template>

<style scoped>
/* 主题只覆盖工作台子树：变量挂在根节点上，控制台其他页面仍用全局浅色 */
.zc-wb[data-theme='dark'] {
  --zc-bg: #1e1e1e;
  --zc-sidebar-bg: #252526;
  --zc-border: #3c3c3c;
  --zc-text: #cccccc;
  --zc-text-sub: #8c8c8c;
}
.zc-wb[data-theme='light'] {
  --zc-bg: #ffffff;
  --zc-sidebar-bg: #f3f3f3;
  --zc-border: #e0e0e0;
  --zc-text: #333333;
  --zc-text-sub: #6b6b6b;
}
.zc-wb {
  display: flex;
  height: 100%;
  min-height: 0;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--zc-bg);
  color: var(--zc-text);
}
.zc-wb-side {
  flex: 0 0 280px;
  width: 280px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--zc-border);
  background: var(--zc-sidebar-bg);
}
.zc-wb-side-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--zc-border);
}
.zc-wb-ws-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
  font-weight: 600;
}
.zc-wb-crumbs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  font-size: 12px;
  color: var(--zc-text-sub);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  border-bottom: 1px solid var(--zc-border);
}
.zc-wb-crumb {
  cursor: pointer;
}
.zc-wb-crumb:hover {
  color: var(--zc-text);
}
.zc-wb-crumb-current {
  cursor: default;
  color: var(--zc-text);
}
.zc-wb-sep {
  color: var(--zc-border);
}
.zc-wb-tree {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 4px;
}
.zc-wb-loading {
  display: block;
  margin: 24px auto;
}
.zc-wb-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  vertical-align: middle;
}
.zc-wb-node-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.zc-wb-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--zc-bg);
}
.zc-wb-tabs {
  flex: 0 0 auto;
  display: flex;
  align-items: stretch;
  overflow-x: auto;
  overflow-y: hidden;
  white-space: nowrap;
  border-bottom: 1px solid var(--zc-border);
  background: var(--zc-sidebar-bg);
}
.zc-wb-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 6px 12px;
  border-right: 1px solid var(--zc-border);
  font-size: 12px;
  color: var(--zc-text-sub);
  cursor: pointer;
  flex: none;
  max-width: 220px;
}
.zc-wb-tab.is-active {
  color: var(--zc-text);
  background: var(--zc-bg);
  box-shadow: inset 0 -2px 0 var(--zc-primary, #1677ff);
}
.zc-wb-tab-title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.zc-wb-tab-close {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
}
.zc-wb-tab-close:hover {
  color: var(--zc-text);
}
.zc-wb-dirty {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.75;
}
.zc-wb-close-text {
  margin: 0;
  font-size: 13px;
}

/* ant-design 默认吃全局浅色 token，深色主题下把树与按钮拉回工作台配色 */
.zc-wb[data-theme='dark'] :deep(.ant-tree) {
  background: transparent;
  color: var(--zc-text);
}
.zc-wb[data-theme='dark'] :deep(.ant-tree .ant-tree-node-content-wrapper) {
  color: var(--zc-text);
}
.zc-wb[data-theme='dark'] :deep(.ant-tree .ant-tree-node-content-wrapper:hover) {
  background: #2a2d2e;
}
.zc-wb[data-theme='dark'] :deep(.ant-tree .ant-tree-node-content-wrapper.ant-tree-node-selected) {
  background: #094771;
  color: #ffffff;
}
.zc-wb[data-theme='dark'] :deep(.ant-tree .ant-tree-switcher) {
  color: var(--zc-text-sub);
}
.zc-wb[data-theme='dark'] :deep(.ant-empty-description) {
  color: var(--zc-text-sub);
}
.zc-wb[data-theme='dark'] :deep(.ant-btn) {
  background: #333333;
  border-color: #3c3c3c;
  color: #cccccc;
}
.zc-wb[data-theme='dark'] :deep(.ant-btn:hover) {
  border-color: #007acc;
  color: #ffffff;
}
</style>
