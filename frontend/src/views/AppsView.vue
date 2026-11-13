<script setup>
// 应用页（M1 卡片/向导/预览抽屉 + M2 发布接线）：发布/回滚进度弹层与发布历史抽屉都只在
// 打开时挂载（v-if）——WS 订阅单实例纪律由 PublishModal 自身的 connect/close 保证；
// 卡片 prod 状态靠既有 5s 轮询刷新，正式地址 hover 才懒加载 appUrls（不轰炸后端）。
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  AppstoreOutlined, CloudUploadOutlined, CopyOutlined, DashboardOutlined,
  DatabaseOutlined, DeleteOutlined, EditOutlined,
  EyeOutlined, FolderOpenOutlined, HistoryOutlined, MenuOutlined, MobileOutlined, PlayCircleOutlined,
  PoweroffOutlined, ProfileOutlined, RedoOutlined, ReloadOutlined, RocketOutlined, ShopOutlined,
} from '@ant-design/icons-vue'
import {
  appUrls, appMetricsSummary, createApp, deleteApp, devReset, devRestart, devStart, devStatus, devStop,
  listApps, openFolder, prodStart, prodStop, releaseToStore, renameApp, validateAppSlug,
} from '../api'
import { fmtBytes, fmtPct } from '../utils/metricsFormat'
import PublishHistory from '../components/PublishHistory.vue'
import MetricsModal from '../components/MetricsModal.vue'
import MobileModal from '../components/MobileModal.vue'
import PublishModal from '../components/PublishModal.vue'
import { LARGE_STACKS, SMALL_STACKS, slugFromName, validateForm } from '../utils/appForm'
import { copyText } from '../utils/clipboard'
import DeployLogPanel from '../components/DeployLogPanel.vue'
import BackButton from '../components/BackButton.vue'
import { openWorkspaceFilesPage, resolveOpenFolderMode } from '../utils/openFolder'
import { canPublish } from '../utils/publishableStacks'
import { isMobileApp } from '../utils/mobileApp'
import { useConfigStore } from '../stores/config'

const apps = ref([])
const loading = ref(true)
const showWizard = ref(false)
const current = ref(0)
const preview = reactive({ open: false, app: null, url: '', status: '', nonce: 0 })
const logs = reactive({ open: false, app: null })
const pub = reactive({ open: false, app: null, mode: 'publish', version: null, autoStart: false, seq: 0 })
const hist = reactive({ open: false, app: null })
const mon = reactive({ open: false, app: null })
const mob = reactive({ open: false, app: null })
const rel = reactive({ open: false, app: null, title: '', summary: '' })
const urlsCache = reactive({})
// 复制失败时的就地兜底：{ key, url }，key 是失败发生的那一处（`card:<id>` / 'preview'）
const copyFail = reactive({ key: '', url: '' })
const rename = reactive({ open: false, app: null, name: '' })
const del = reactive({ open: false, app: null })
const openingFolder = ref(false)
const cfg = useConfigStore()
// 仪表带数据：整页一次批量取（appMetricsSummary），按 app_id 贴回卡片。
// window 只认后端回的那个值——曲线口径有两处写法时，前端这处迟早过期。
const msum = reactive({ window: '1h', items: [] })
// 与后端 LIVE_PROD_STATUSES 对齐：停炉后 cpu/memory 没有实时值，画成 0 就是撒谎。
const LIVE_PROD = ['running', 'unhealthy']
const RING_C = 97.39            // r=15.5 的周长（环只画 0-100，超出的部分留给下面的真实数字）
const NO_SAMPLE = '未采集'
const NOT_LIVE = '停炉未采集'
const form = reactive({ name: '', slug: '', slugTouched: false, scale: 'small', stack: 'vue3-fastapi' })
let timer = null
let metricsTimer = null

const scaleCards = [
  { value: 'small', title: '小型', desc: '表单/台账/查询类，选一种轻量后端' },
  { value: 'large', title: '大型', desc: '多角色/流程类系统，Vue3 + Spring Boot 3（可选 uni-app 移动端）' },
]

// silent=true：后台 5s 轮询只悄悄换数据、不翻 loading 态——否则每 5 秒整片卡片被 <a-spin>
// 顶掉再重渲染，用户眼里就是「页面一直在刷新」。首屏与手动「刷新」仍走带 loading 的正常路径，
// 轮询期间的报错也不弹（避免后端抖动时刷屏）。
async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    apps.value = (await listApps()).data
  } catch (e) {
    if (!silent) message.error(e?.response?.data?.detail || '应用列表加载失败')
  } finally {
    if (!silent) loading.value = false
  }
}
// 仪表带的曲线桶宽 150 秒（后端 SUMMARY_WINDOW/SUMMARY_MAX_POINTS 决定），30 秒一轮已经追得上
// 真实变化；状态那一路保持 5 秒静默轮询不变。两条定时分开是刻意的：绑在一起要么曲线白拉，
// 要么状态变钝。
onMounted(() => {
  load()
  loadSummary()
  timer = setInterval(() => load(true), 5000)
  metricsTimer = setInterval(loadSummary, 30000)
})
onUnmounted(() => { clearInterval(timer); clearInterval(metricsTimer) })

async function loadSummary() {
  try {
    const { data } = await appMetricsSummary()
    msum.window = data?.window || '1h'
    msum.items = data?.items || []
  } catch { /* 静默：仪表带退成「未采集」就行，卡片上的动作一个都不该因此失灵 */ }
}

// 每张卡的仪表读数：一次算好贴到卡片上（模板里反复取值会每张卡重算六次）。
const bands = computed(() => Object.fromEntries(apps.value.map((a) => [a.id, readout(a)])))
const band = (a) => bands.value[a.id] || readout(a)

function readout(a) {
  const it = msum.items.find((x) => x.app_id === a.id)
  const live = !!it && LIVE_PROD.includes(it.prod_status)
  const cpu = it?.cpu ? Number(it.cpu.value) : null
  const none = live ? NO_SAMPLE : NOT_LIVE
  return {
    live,
    cpu,
    cpuText: it?.cpu ? fmtPct(it.cpu.value) : none,
    memText: it?.memory ? fmtBytes(it.memory.value) : none,
    dbText: it?.db_size ? fmtBytes(it.db_size.value) : NO_SAMPLE,
    wsText: it?.workspace_size ? fmtBytes(it.workspace_size.value) : NO_SAMPLE,
    cpuSpark: sparkPath(it?.series?.cpu),
    memSpark: sparkPath(it?.series?.memory),
    ringOffset: RING_C * (1 - Math.min(Math.max(cpu || 0, 0), 100) / 100),
  }
}

// sparkline 路径：桶点归一到 100×26 视口。少于两点就不画——一条贴底的直线会被读成
// 「一直在这么闲」，比留白更容易骗人。
function sparkPath(points, w = 100, h = 26) {
  const vals = (points || []).map((p) => Number(p?.value)).filter(Number.isFinite)
  if (vals.length < 2) return ''
  const min = Math.min(...vals)
  const span = Math.max(...vals) - min || 1
  const step = w / (vals.length - 1)
  return vals.map((v, i) =>
    `${i ? 'L' : 'M'}${(i * step).toFixed(1)} ${(h - 2 - ((v - min) / span) * (h - 4)).toFixed(1)}`).join(' ')
}

// 页头统计：prod 状态四桶必须严丝合缝盖全（running / starting+publishing / stopped / unhealthy+error），
// 漏一桶就会出现「四个数加起来不等于总数」——列表示意里最容易骗人的正是这种漏。
// 平均 CPU 只计采到值的活应用：一个都没采到写 —，写 0.0 % 会被读成「很闲」。
const STAT_CELLS = [
  { key: 'total', label: '应用' },
  { key: 'running', label: '运行中', color: '#5b8a72' },
  { key: 'busy', label: '进行中', color: 'var(--zc-primary)' },
  { key: 'stopped', label: '已停止', color: '#9aa0a6' },
  { key: 'bad', label: '异常', color: '#b96a6a' },
  { key: 'cpuText', label: '平均 CPU' },
]
const stats = computed(() => {
  const n = { total: apps.value.length, running: 0, busy: 0, stopped: 0, bad: 0 }
  for (const a of apps.value) {
    const s = a.status_prod || 'stopped'
    if (s === 'unhealthy' || s === 'error') n.bad += 1
    else if (s === 'starting' || s === 'publishing') n.busy += 1
    else if (s === 'running') n.running += 1
    else n.stopped += 1
  }
  const cpus = msum.items.map((it) => (it.cpu ? Number(it.cpu.value) : null)).filter(Number.isFinite)
  n.cpuText = cpus.length ? fmtPct(cpus.reduce((x, y) => x + y, 0) / cpus.length) : '—'
  return n
})

function onName() {
  if (!form.slugTouched) form.slug = slugFromName(form.name)
}
async function onSlug() {
  form.slugTouched = true
  if (!form.slug) return
  try {
    const r = (await validateAppSlug(form.slug)).data
    if (!r.ok) message.warning(`该 slug 不可用，建议：${r.suggest}`)
  } catch (e) {
    message.error(e?.response?.data?.detail || 'slug 校验失败')
  }
}
function nextFrom1() {
  if (!form.name.trim()) return message.warning('请填写应用名称')
  if (!/^[a-z][a-z0-9]{1,39}$/.test(form.slug)) return message.warning('slug 非法')
  current.value = 1
}
function nextFrom2() {
  // 换档时把栈拉回本档合法值：large→small 之后还停在 springboot 会被后端 400 拒掉
  if (form.scale === 'large' && !LARGE_STACKS.some((s) => s.value === form.stack)) {
    form.stack = 'vue3-springboot'
  } else if (form.scale === 'small' && !SMALL_STACKS.some((s) => s.value === form.stack)) {
    form.stack = 'vue3-fastapi'
  }
  current.value = 2
}
async function submit() {
  const v = validateForm(form)
  if (!v.ok) return message.warning(v.msg)
  try {
    await createApp({ name: form.name.trim(), slug: form.slug, scale: form.scale, stack: form.stack })
    message.success('应用已创建')
    showWizard.value = false
    Object.assign(form, { name: '', slug: '', slugTouched: false, scale: 'small', stack: 'vue3-fastapi' })
    current.value = 0
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '创建失败')
  }
}

const statusColor = (s) => ({
  running: '#5b8a72', stopped: '#9aa0a6', starting: '#5b7a9d',
  publishing: '#5b7a9d', error: '#b96a6a', unhealthy: '#c08a3e',
}[s || 'stopped'])
const statusText = (s) => ({
  running: '运行中', stopped: '已停止', starting: '启动中…',
  publishing: '发布中…', error: '出错了', unhealthy: '体检未通过',
}[s || 'stopped'])
// 环境胶囊里只画图标，动词全称只有一份来源：aria-label 与悬停气泡共用它，免得两处各写各的。
const devToggleLabel = (a) => (a.status_dev === 'running' ? '停止预览' : '启动预览')
const prodToggleLabel = (a) => (a.status_prod === 'running' ? '停止正式' : '启动正式')

async function openPreview(a) {
  preview.open = true
  preview.app = a
  await refreshPreview(a)
}
async function refreshPreview(a) {
  try {
    const st = (await devStatus(a.id)).data
    preview.status = st.status_dev
    preview.url = st.url
    preview.nonce++
  } catch (e) {
    message.error(e?.response?.data?.detail || '预览状态获取失败')
  }
}
async function toggleDev(a) {
  try {
    if (a.status_dev === 'running') {
      await devStop(a.id)
      message.success('已停止预览')
    } else {
      await devStart(a.id)
      message.info('预览启动中，稍候自动刷新')
    }
    setTimeout(load, 1200)
  } catch (e) {
    message.error(e?.response?.data?.detail || '操作失败')
  }
}
async function restartDev(a) {
  try {
    await devRestart(a.id)
    message.info('重启中…')
    setTimeout(load, 1500)
  } catch (e) {
    message.error(e?.response?.data?.detail || '重启失败')
  }
}
async function resetDev(a) {
  // 破坏性：drop 并重建 <slug>_dev，从 sqlchange 文件全量 fold；prod 不受影响。
  Modal.confirm({
    title: '重建 dev 数据库',
    content: `将删除并重建 ${a.slug}_dev（从 sqlchange 文件全量重放），用于消除 dev 漂移；不影响 ${a.slug}_prod。确认继续？`,
    okText: '重建',
    okType: 'danger',
    onOk: async () => {
      try {
        await devReset(a.id)
        message.info('dev 重建已受理，进度见查看日志')
      } catch (e) {
        message.error(e?.response?.data?.detail || '重建失败')
      }
    },
  })
}
async function copyUrl(url, key = '') {
  // navigator.clipboard 只在安全上下文（https 或 localhost）存在：用 IP + http 访问控制台时它是
  // undefined。utils/clipboard.js 里依次降级到 execCommand('copy')，两条路都不通才算失败。
  if (await copyText(url)) {
    copyFail.key = ''
    copyFail.url = ''
    message.success('已复制')
    return
  }
  // 失败时把地址就地摊开：卡片上的「正式地址」从来不是一个可见文本（只有一个复制动作），
  // 光说「请手动选择地址」等于把用户支到一个不存在的地方。key 让兜底只出现在失败的那一处。
  copyFail.key = key
  copyFail.url = url || ''
  message.error('复制失败，请手动选择地址')
}
// 查看日志：独立抽屉，栈没起也能打开（上一次启动失败在哪一步，只有这里看得到）
function openLogs(a) {
  logs.app = a
  logs.open = true
}

// ---- 卡片 ☰ 菜单：重命名 / 打开工程目录 / 复制预览地址 / 查看日志 / 删除 ----
function openRename(a) {
  rename.app = a
  rename.name = a.name
  rename.open = true
}
const renameOk = computed(() => !!rename.name.trim())
async function doRename() {
  if (!rename.app || !renameOk.value) return
  try {
    await renameApp(rename.app.id, rename.name.trim())
    message.success('已重命名')
    rename.open = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '重命名失败')
  }
}

// 打开工程目录：与右栏文件面板同一套判断——本机直开系统文件管理器，
// 远端访问（服务端开窗用户看不见）或直开失败就当场降级到内置文件管理器整页。
// 整页走新标签（与 DB 操作同一个口径）：卡片列表的展开态与滚动位置都不被打断。
function openFilesPage(a) {
  if (!openWorkspaceFilesPage(a.workspace_id, a.name)) {
    message.warning('新标签没能打开（可能被浏览器拦截），请允许本站弹出窗口后重试')
  }
}

async function openAppFolder(a) {
  if (openingFolder.value) return
  openingFolder.value = true
  try {
    await cfg.ensureLoaded()
    if (resolveOpenFolderMode(cfg.form.openFolderMode, window.location.hostname) === 'web') {
      openFilesPage(a)
      return
    }
    await openFolder(a.workspace_id)
    message.success('已在系统文件管理器中打开工程目录')
  } catch (e) {
    openFilesPage(a)
    message.info('无法直接打开文件夹，已改用内置文件管理器')
  } finally {
    openingFolder.value = false
  }
}

function openDelete(a) {
  del.app = a
  del.open = true
}
// 卡片上的删除走一次确认：只删平台记录与会话，工程目录与数据库都还在，
// 确认文案必须把「删了什么、留了什么」说清，而不是加一道打字闸门。
async function doDelete() {
  if (!del.app) return
  const a = del.app
  try {
    const { data } = await deleteApp(a.id)
    const kept = data?.kept
    message.success(kept?.root_path ? `应用已删除，工程目录保留在 ${kept.root_path}` : '应用已删除')
    del.open = false
    closeOverlays(a.id)
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

// 应用没了还留着它的抽屉：预览/日志面板会继续拿死 id 轮询状态，只会刷出一串错误
function closeOverlays(appId) {
  if (preview.open && preview.app?.id === appId) preview.open = false
  if (logs.open && logs.app?.id === appId) {
    logs.open = false
    logs.app = null
  }
}

// ---------- M2 发布接线 ----------
function openPublish(a) {
  Object.assign(pub, { app: a, mode: 'publish', version: null, autoStart: false,
    seq: pub.seq + 1, open: true })
}
function openHistory(a) {
  hist.app = a
  hist.open = true
}
// 资源监控弹框（重设计）：居中 a-modal 承载 MetricsModal（分组 2×2 卡片 + 精致曲线）。
function openMetrics(a) {
  mon.app = a
  mon.open = true
}
// 移动端弹框：卡面 ☰ 与聊天页「更多」共用 MobileModal（自持 a-modal），这里只传 app。
function openMobile(a) {
  mob.app = a
  mob.open = true
}
// 历史页确认回滚 → 关掉抽屉、开进度弹层（autoStart 单点触发 rollbackApp）
function startRollback(version) {
  const app = hist.app
  hist.open = false
  Object.assign(pub, { app, mode: 'rollback', version, autoStart: true,
    seq: pub.seq + 1, open: true })
}
async function toggleProd(a) {
  try {
    if (a.status_prod === 'running') {
      await prodStop(a.id)
      message.success('正式环境已停止')
    } else {
      await prodStart(a.id)
      message.info('正式环境启动中，稍候自动刷新')
    }
    setTimeout(load, 1200)
  } catch (e) {
    message.error(e?.response?.data?.detail || '操作失败')
  }
}
async function ensureUrls(a) {
  if (urlsCache[a.id]) return
  try {
    urlsCache[a.id] = (await appUrls(a.id)).data
  } catch { /* hover 懒加载失败静默，卡片仍有端口兜底 */ }
}
async function copyProdUrl(a) {
  await ensureUrls(a)
  // 拿不到就如实说，不拼一个域名兜底（ADR-0016：域名的事实来源只在后端一处）。两条路径都会
  // 走到这里：ensureUrls 把请求异常静默吞掉了（hover 懒加载失败不该打扰用户），而后端在 slug
  // 缺位时**故意**回空串。兜底拼出来的地址是条死链，而它和真地址在界面上无法区分。
  const url = urlsCache[a.id]?.domain
  if (!url) {
    message.warning('正式地址暂时取不到，请稍后重试')
    return
  }
  await copyUrl(url, `card:${a.id}`)
}

// ---------- M3-a 商店：发布到商店（只认真实在服的 prod 版本，闸门在后端） ----------
const canReleaseToStore = (a) => canPublish(a) && !!a.current_version && !a.store
function openRelease(a) {
  Object.assign(rel, { app: a, title: a.name, summary: '', open: true })
}
async function doRelease() {
  if (!rel.app) return
  try {
    await releaseToStore(rel.app.id, { title: rel.title.trim() || rel.app.name, summary: rel.summary })
    message.success('已上架商店，可在侧栏「商店」页看到')
    rel.open = false
    await load()
  } catch (e) {
    const d = e?.response?.data?.detail
    message.error(typeof d === 'string' ? d : '上架失败')
  }
}
</script>

<template>
  <div class="zc-apps">
    <div class="zc-hero">
      <div class="zc-head">
        <div class="zc-head-left">
          <back-button />
          <h2><appstore-outlined /> 我的应用</h2>
          <span class="zc-hero-count">{{ stats.total }}</span>
        </div>
        <a-space>
          <a-button @click="load()"><reload-outlined /> 刷新</a-button>
          <a-button type="primary" @click="showWizard = true"><rocket-outlined /> 新建应用</a-button>
        </a-space>
      </div>
      <div class="zc-stats">
        <span v-for="c in STAT_CELLS" :key="c.key" class="zc-stat"
              :style="c.color ? { color: c.color } : null">
          <b class="zc-stat-value">{{ stats[c.key] }}</b>
          <span class="zc-stat-label">{{ c.label }}</span>
        </span>
      </div>
    </div>

    <a-spin v-if="loading" class="zc-spin" />
    <a-empty v-else-if="!apps.length" description="还没有应用——点「新建应用」，用一句话描述你要的软件"
             style="margin-top: 80px" />
    <div v-else class="zc-grid">
      <a-card v-for="a in apps" :key="a.id" class="zc-card zc-metric-card"
              :style="{ '--zc-rail': statusColor(a.status_prod) }">
        <template #title>
          <span class="zc-dot" :style="{ background: statusColor(a.status_dev) }" />
          <span class="zc-card-name">{{ a.name }}</span>
          <a-tag v-if="a.store" class="zc-store-tag">来自商店</a-tag>
          <a-tag class="zc-card-tag">{{ a.scale === 'large' ? '大型' : '小型' }} · {{ a.stack }}</a-tag>
        </template>
        <template #extra>
          <a-dropdown :trigger="['click']" placement="bottomRight">
            <menu-outlined class="zc-card-menu-btn" title="破坏性操作" @click.stop />
            <template #overlay>
              <div class="zc-app-menu">
                <!-- ☰ 只剩这两个会删数据/删应用的动作。平铺进工具条等于把误点的代价摊到每次点击上，
                     而它们本来各自带一次确认框，收在这里是对的。 -->
                <div class="zc-app-menu-item zc-menu-reset" @click.stop="resetDev(a)"><redo-outlined /> 重建 dev</div>
                <div class="zc-app-menu-item zc-app-menu-danger zc-menu-del" @click.stop="openDelete(a)">
                  <delete-outlined /> 删除应用
                </div>
              </div>
            </template>
          </a-dropdown>
        </template>
        <p class="zc-line">
          预览：{{ statusText(a.status_dev) }} · 端口 {{ a.dev_port }}
          <a v-if="a.status_dev === 'running'" :href="a.preview_url"
             target="_blank">打开</a>
        </p>
        <p class="zc-line sub" @mouseenter="ensureUrls(a)">
          正式：<span class="zc-dot" :style="{ background: statusColor(a.status_prod) }" />{{ statusText(a.status_prod) }}
          <template v-if="a.current_version"> · {{ a.current_version }}</template>
          <a v-if="a.status_prod === 'running'" href="#" @click.prevent="copyProdUrl(a)">
            <copy-outlined /> 正式地址
          </a>
        </p>
        <!-- 复制失败才出现的兜底行：user-select: all 让单击即整段选中，键盘 Ctrl+C 就能拿走 -->
        <p v-if="copyFail.key === `card:${a.id}`" class="zc-line zc-copy-manual">{{ copyFail.url }}</p>
        <!-- 仪表带：CPU 环 + 近窗曲线 + 三个字节读数。全是后端采集值；停炉没有实时值就照实空着，
             不拿 0.0 % 冒充「很闲」。 -->
        <div class="zc-band">
          <div class="zc-gauge" :class="{ off: !band(a).live }"
               title="CPU 占用：prod 栈所有常驻容器汇总，多核可超 100%（环只画到 100，数字照实）">
            <svg class="zc-ring" viewBox="0 0 36 36" aria-hidden="true">
              <circle class="zc-ring-track" cx="18" cy="18" r="15.5" />
              <circle class="zc-ring-fill" cx="18" cy="18" r="15.5" :stroke-dasharray="RING_C"
                      :stroke-dashoffset="band(a).ringOffset" transform="rotate(-90 18 18)" />
            </svg>
            <div class="zc-gauge-body">
              <span class="zc-gauge-value">{{ band(a).cpuText }}</span>
              <span class="zc-gauge-label">CPU</span>
            </div>
          </div>
          <div class="zc-sparks">
            <div class="zc-spark zc-spark-cpu">
              <span class="zc-spark-label">CPU 近 {{ msum.window }}</span>
              <svg v-if="band(a).cpuSpark" class="zc-spark-svg" viewBox="0 0 100 26"
                   preserveAspectRatio="none" aria-hidden="true"><path :d="band(a).cpuSpark" /></svg>
              <span v-else class="zc-spark-none">无采样</span>
            </div>
            <div class="zc-spark zc-spark-mem">
              <span class="zc-spark-label">内存 近 {{ msum.window }}</span>
              <svg v-if="band(a).memSpark" class="zc-spark-svg" viewBox="0 0 100 26"
                   preserveAspectRatio="none" aria-hidden="true"><path :d="band(a).memSpark" /></svg>
              <span v-else class="zc-spark-none">无采样</span>
            </div>
          </div>
          <div class="zc-stores">
            <span>内存 <b class="zc-metric-mem">{{ band(a).memText }}</b></span>
            <span>数据库 <b class="zc-metric-db">{{ band(a).dbText }}</b></span>
            <span>工作区 <b class="zc-metric-ws">{{ band(a).wsText }}</b></span>
          </div>
        </div>
        <!-- 操作仍在一行，但按环境切成两段：测试环境（dev，直改直跑的预览实例）| 生产环境（prod，已发布版本）。
             中间一条竖线 + 各段环境标签，避免同名动作被误读成跨环境操作。 -->
        <!-- 环境动作行：两段各成一颗胶囊（状态点 + dev/prod + 两个图标动作），一行排完不折行。
             分组交给胶囊外壳，中文全称退回 title 与按钮气泡——行宽只有 440px，撑不起四个全称按钮。 -->
        <div class="zc-acts">
          <div class="zc-env-group zc-env-dev"
               title="测试环境（dev）：直接跑工程目录里的代码，独立 dev 端口与 _dev 库">
            <i class="zc-env-dot" :style="{ background: statusColor(a.status_dev) }" />
            <span class="zc-env-name">dev</span>
            <button type="button" class="zc-env-btn" :aria-label="devToggleLabel(a)" @click="toggleDev(a)">
              <poweroff-outlined v-if="a.status_dev === 'running'" />
              <play-circle-outlined v-else /><span class="zc-tb-label">{{ devToggleLabel(a) }}</span>
            </button>
            <button type="button" class="zc-env-btn" aria-label="重启预览" @click="restartDev(a)">
              <reload-outlined /><span class="zc-tb-label">重启</span>
            </button>
          </div>
          <div class="zc-env-group zc-env-prod"
               title="生产环境（prod）：跑已发布的版本，独立 prod 端口与 _prod 库">
            <i class="zc-env-dot" :style="{ background: statusColor(a.status_prod) }" />
            <span class="zc-env-name">prod</span>
            <button type="button" class="zc-env-btn zc-env-btn-prim zc-act-publish" :disabled="!canPublish(a)"
                    aria-label="发布新版本"
                    :title="canPublish(a) ? '发布新版本' : '该栈暂不支持发布'"
                    :aria-busy="a.status_prod === 'publishing' ? 'true' : 'false'"
                    :class="{ 'is-busy': a.status_prod === 'publishing' }" @click="openPublish(a)">
              <reload-outlined v-if="a.status_prod === 'publishing'" spin />
              <cloud-upload-outlined v-else /><span class="zc-tb-label">发布</span>
            </button>
            <button type="button" class="zc-env-btn" :aria-label="prodToggleLabel(a)" @click="toggleProd(a)">
              <poweroff-outlined v-if="a.status_prod === 'running'" />
              <play-circle-outlined v-else /><span class="zc-tb-label">{{ prodToggleLabel(a) }}</span>
            </button>
          </div>
        </div>
        <!-- 工具条常驻显示（不靠 hover、不靠点开）：动作节点一直在 DOM 里，文字标签做成悬停气泡——
             它同时是 accessible name。触屏与键盘都不必先「发现这里有东西」。 -->
        <div class="zc-toolbar" role="group" aria-label="更多操作">
          <button type="button" class="zc-tb-item zc-menu-preview" @click.stop="openPreview(a)">
            <eye-outlined /><span class="zc-tb-label">预览</span></button>
          <button type="button" class="zc-tb-item zc-menu-copy" @click.stop="copyUrl(a.preview_url, `card:${a.id}`)">
            <copy-outlined /><span class="zc-tb-label">复制预览地址</span></button>
          <button type="button" class="zc-tb-item zc-menu-metrics" @click.stop="openMetrics(a)">
            <dashboard-outlined /><span class="zc-tb-label">资源监控</span></button>
          <button type="button" class="zc-tb-item zc-menu-history" @click.stop="openHistory(a)">
            <history-outlined /><span class="zc-tb-label">发布历史</span></button>
          <button type="button" class="zc-tb-item zc-menu-folder" @click.stop="openAppFolder(a)">
            <folder-open-outlined /><span class="zc-tb-label">打开工程目录</span></button>
          <a class="zc-tb-item zc-menu-db" :href="`/apps/${a.id}/db`" target="_blank" @click.stop>
            <database-outlined /><span class="zc-tb-label">DB 操作</span></a>
          <button type="button" class="zc-tb-item zc-menu-logs" @click.stop="openLogs(a)">
            <profile-outlined /><span class="zc-tb-label">查看日志</span></button>
          <button type="button" class="zc-tb-item zc-menu-rename" @click.stop="openRename(a)">
            <edit-outlined /><span class="zc-tb-label">重命名</span></button>
          <button v-if="isMobileApp(a)" type="button" class="zc-tb-item zc-menu-mobile" @click.stop="openMobile(a)">
            <mobile-outlined /><span class="zc-tb-label">移动端</span></button>
          <button v-if="canReleaseToStore(a)" type="button" class="zc-tb-item zc-menu-release" @click.stop="openRelease(a)">
            <shop-outlined /><span class="zc-tb-label">发布到商店</span></button>
        </div>
      </a-card>
    </div>

    <a-modal v-model:open="showWizard" title="新建应用" :footer="null" width="560px" destroy-on-close>
      <a-steps :current="current" size="small" style="margin-bottom: 20px"
               :items="[{ title: '起名字' }, { title: '选规模' }, { title: '选技术栈' }]" />
      <div v-if="current === 0">
        <a-form layout="horizontal" :label-col="{ style: { width: '120px', textAlign: 'right' } }">
          <a-form-item label="应用名称" required>
            <a-input v-model:value="form.name" placeholder="如：来料登记" @input="onName" />
          </a-form-item>
          <a-form-item label="应用标识" required help="小写字母开头，仅小写字母/数字，2-40 位">
            <a-input v-model:value="form.slug" placeholder="如：lailiao" @blur="onSlug" />
          </a-form-item>
        </a-form>
        <div class="zc-wizard-foot">
          <a-button type="primary" @click="nextFrom1">下一步</a-button>
        </div>
      </div>
      <div v-else-if="current === 1">
        <div v-for="s in scaleCards" :key="s.value" class="zc-pick" :class="{ on: form.scale === s.value }"
             @click="form.scale = s.value">
          <b>{{ s.title }}</b><span>{{ s.desc }}</span>
        </div>
        <div class="zc-wizard-foot">
          <a-button style="margin-right: 8px" @click="current = 0">上一步</a-button>
          <a-button type="primary" @click="nextFrom2">下一步</a-button>
        </div>
      </div>
      <div v-else>
        <a-form layout="horizontal" :label-col="{ style: { width: '120px', textAlign: 'right' } }">
          <a-form-item label="技术栈" required>
            <a-select v-model:value="form.stack" :options="form.scale === 'large'
              ? LARGE_STACKS
              : SMALL_STACKS" />
          </a-form-item>
        </a-form>
        <p class="zc-tip">数据库将自动使用 PostgreSQL（dev / prod 两套独立库）。</p>
        <p v-if="form.scale === 'large'" class="zc-tip">已预置登录、RBAC 菜单、部门/字典/操作日志基座（admin / admin123，登录后请改密）。首次预览需在容器内下载依赖，约 5–15 分钟，请等到状态变绿后再刷新。</p>
        <div class="zc-wizard-foot">
          <a-button style="margin-right: 8px" @click="current = 1">上一步</a-button>
          <a-button type="primary" @click="submit">创建应用</a-button>
        </div>
      </div>
    </a-modal>

    <a-drawer v-model:open="preview.open" :title="`预览 · ${preview.app?.name || ''}`" width="80%">
      <a-space style="margin-bottom: 12px">
        <a-tag :color="preview.status === 'running' ? 'green' : 'default'">{{ statusText(preview.status) }}</a-tag>
        <a-button size="middle" @click="preview.app && refreshPreview(preview.app)"><reload-outlined /> 刷新</a-button>
        <a-button size="middle" v-if="preview.url" @click="copyUrl(preview.url, 'preview')">
          <copy-outlined /> 复制地址
        </a-button>
        <a-button size="middle" v-if="preview.url" target="_blank" :href="preview.url">新窗口打开</a-button>
      </a-space>
      <p v-if="copyFail.key === 'preview'" class="zc-line zc-copy-manual">{{ copyFail.url }}</p>
      <iframe v-if="preview.status === 'running' && preview.url"
              :key="preview.nonce" :src="preview.url"
              style="width: 100%; height: calc(100vh - 160px); border: 1px solid #e5e7eb; border-radius: 4px" />
      <a-empty v-else description="预览未启动——点卡片上的「启动预览」，或稍候刷新" />
    </a-drawer>

    <a-drawer v-model:open="logs.open" :title="`日志 · ${logs.app?.name || ''}`" width="62%"
              :body-style="{ padding: '12px 16px' }" destroy-on-close>
      <deploy-log-panel v-if="logs.app" :app-id="logs.app.id" :app-name="logs.app.name"
                        :active="logs.open" />
    </a-drawer>

    <PublishModal v-if="pub.open" :key="pub.seq" v-model:open="pub.open" :app="pub.app"
                  :mode="pub.mode" :rollback-version="pub.version" :auto-start="pub.autoStart"
                  @done="load" />

    <a-drawer v-model:open="hist.open" :title="`发布历史 · ${hist.app?.name || ''}`" width="760px">
      <PublishHistory v-if="hist.open && hist.app" :app="hist.app" @rollback="startRollback" />
    </a-drawer>

    <a-modal v-model:open="mon.open" :width="920" :centered="true" :footer="null" destroy-on-close>
      <template #title>
        <span class="zc-metrics-title">
          资源监控 · {{ mon.app?.name || '' }}
          <a-tag :color="statusColor(mon.app?.status_prod)" class="zc-metrics-badge">{{ statusText(mon.app?.status_prod) }}</a-tag>
        </span>
      </template>
      <MetricsModal v-if="mon.open && mon.app" :key="mon.app.id" :app="mon.app" />
    </a-modal>

    <MobileModal v-if="mob.open && mob.app" :key="mob.app.id" v-model:open="mob.open" :app="mob.app" />

    <a-modal v-model:open="rel.open" title="发布到商店" ok-text="上架" destroy-on-close @ok="doRelease">
      <a-input v-model:value="rel.title" placeholder="商店展示名称" />
      <a-textarea v-model:value="rel.summary" placeholder="一句话简介（可选）" :rows="3"
                  style="margin-top: 8px" />
      <p class="zc-modal-tip">
        上架的是当前正式在服版本 {{ rel.app?.current_version ? `（${rel.app.current_version}）` : '' }}，
        含真实执行过的数据变更序列；获批者会得到独立代码与独立数据库的实例。
      </p>
    </a-modal>

    <a-modal v-model:open="rename.open" title="重命名应用" ok-text="保存" destroy-on-close
             class="zc-rename-modal" :ok-button-props="{ disabled: !renameOk }" @ok="doRename">
      <a-input v-model:value="rename.name" placeholder="应用名称" class="zc-rename-input" />
      <p class="zc-modal-tip">只改显示名；应用标识（{{ rename.app?.slug }}）与工程目录保持不变。</p>
    </a-modal>

    <a-modal v-model:open="del.open" title="删除应用" ok-text="确认删除" ok-type="danger" destroy-on-close
             class="zc-del-modal" @ok="doDelete">
      <p class="zc-modal-warn">将删除应用「{{ del.app?.name }}」及其会话记录，运行中的预览与正式环境会一并停止。</p>
      <p class="zc-modal-tip">
        工程目录保留在 {{ del.app?.root_path }}，数据库 {{ del.app?.slug }}_dev / {{ del.app?.slug }}_prod
        也保留；如需彻底清理，请自行删除目录与库。
      </p>
    </a-modal>
  </div>
</template>

<style scoped>
.zc-apps { padding: 20px 24px; height: 100%; overflow-y: auto; box-sizing: border-box; }
/* 页头总控区：一条淡主色渐变把「标题 + 计数 + 状态分布」兜成整块，与下面的卡片彻底分层
   （ADR-0048 D）。渐变收在 .13→.02，再浓就要盖过卡片里的仪表带了。 */
.zc-hero { margin-bottom: 16px; padding: 14px 16px 12px; border: 1px solid var(--zc-border); border-radius: 10px;
           background: linear-gradient(105deg, rgba(91, 122, 157, 0.13), rgba(91, 122, 157, 0.02) 58%,
                                         rgba(196, 154, 68, 0.05)); }
.zc-head { display: flex; justify-content: space-between; align-items: center; }
.zc-head-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.zc-head h2 { margin: 0; font-size: 22px; font-weight: 600; color: var(--zc-text); white-space: nowrap; }
.zc-hero-count { flex-shrink: 0; padding: 2px 9px; border-radius: 10px; font-size: 12px; font-weight: 500;
                 line-height: 1.5; color: var(--zc-primary); background: rgba(91, 122, 157, 0.12);
                 font-variant-numeric: tabular-nums; }
/* 返回做成描边 chip：住在渐变里时，裸文字链接的存在感比不过 22px 标题 */
.zc-hero .zc-back { padding: 2px 10px 2px 7px; border: 1px solid var(--zc-border); border-radius: 14px;
                    background: #fff; }
.zc-stats { display: flex; flex-wrap: wrap; gap: 8px 22px; margin-top: 12px; }
.zc-stat { display: flex; flex-direction: column; gap: 2px; min-width: 52px; }
.zc-stat-value { font-size: 18px; font-weight: 600; line-height: 1.1; color: inherit;
                 font-variant-numeric: tabular-nums; }
.zc-stat-label { font-size: 11px; color: var(--zc-text-sub); }
.zc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr)); gap: 14px; }
.zc-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; flex-shrink: 0; }
/* 标题行拉成 flex：状态点固定、应用名可省略号、规格标签贴着名字（antd 的 .ant-card-head-title 里） */
.zc-card :deep(.ant-card-head-title) { display: flex; align-items: center; overflow: hidden; }
.zc-card-name { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.zc-card-tag { flex-shrink: 0; margin-inline-start: 8px; font-weight: 400; }
.zc-line { margin: 0 0 6px; color: var(--zc-text); font-size: 13px; }
.zc-line.sub { color: var(--zc-text-sub); }
/* 复制失败后的兜底地址：单击即整段选中（user-select: all），等宽 + 折行保证长域名可读 */
.zc-copy-manual { user-select: all; cursor: text; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                  word-break: break-all; background: rgba(91, 122, 157, 0.08); border-radius: 4px; padding: 2px 6px; }
/* 环境动作行：两段各一颗胶囊——状态点 + 字母名 + 两个图标动作，整条约 250px，稳稳落在一行。
   分组靠胶囊外壳，不再靠中间竖线；中文全称退到 title 与按钮气泡。 */
.zc-acts { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.zc-env-group { display: inline-flex; align-items: center; gap: 4px; height: 30px; padding: 0 5px 0 10px;
                border: 1px solid var(--zc-border); border-radius: 15px; background: #fff;
                transition: border-color 0.15s, box-shadow 0.15s; }
.zc-env-group:hover { box-shadow: 0 1px 5px rgba(47, 58, 71, 0.08); }
.zc-env-dev { border-color: rgba(91, 122, 157, 0.30); }
.zc-env-prod { border-color: rgba(196, 154, 68, 0.34); }
.zc-env-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.zc-env-name { margin-inline-end: 2px; font-size: 11px; font-weight: 600; line-height: 1; letter-spacing: 0.04em; }
.zc-env-dev .zc-env-name { color: var(--zc-primary); }
.zc-env-prod .zc-env-name { color: #9a7b3f; }
.zc-env-btn { position: relative; display: inline-flex; align-items: center; justify-content: center;
              width: 27px; height: 26px; padding: 0; border: 1px solid transparent; border-radius: 7px;
              background: none; color: var(--zc-text-sub); font-size: 14px; line-height: 1; cursor: pointer;
              transition: color 0.15s, background 0.15s, border-color 0.15s; }
.zc-env-btn:hover { color: var(--zc-primary); background: rgba(91, 122, 157, 0.10); }
.zc-env-btn:focus-visible { outline: 2px solid var(--zc-primary); outline-offset: 1px; }
.zc-env-btn:disabled { color: var(--zc-text-sub); cursor: not-allowed; opacity: 0.45; }
/* 发布是这段里唯一有后果的动作，常驻主色（置灰时由 :disabled 接管） */
.zc-env-btn-prim { color: var(--zc-primary); }
.zc-spin { display: block; margin: 60px auto; }
.zc-pick { border: 1px solid var(--zc-border); border-radius: 6px; padding: 10px 14px; margin-bottom: 10px;
           cursor: pointer; display: flex; flex-direction: column; gap: 4px; }
.zc-pick.on { border-color: var(--zc-primary); background: rgba(91, 122, 157, 0.08); }
.zc-pick span { color: var(--zc-text-sub); font-size: 12px; }
.zc-wizard-foot { display: flex; justify-content: flex-end; margin-top: 16px; }
.zc-tip { color: var(--zc-text-sub); font-size: 12px; margin: 8px 0 0; }
.zc-modal-tip { margin: 8px 0 0; color: var(--zc-text-sub); font-size: 12px; line-height: 1.6; }
.zc-store-tag { margin-inline-start: 8px; font-weight: 400; vertical-align: middle; }

/* 菜单入口用三横（☰）而不是 ⋯ 的圆点：圆点在卡片头部太小、几乎看不见。
   字号抬到 16px、默认透明度 0.7，hover 提亮为主色（与侧栏同一套低饱和语言）。 */
.zc-card-menu-btn { flex-shrink: 0; font-size: 16px; line-height: 1; padding: 3px 4px; cursor: pointer;
                    opacity: 0.7; transition: opacity 0.15s; color: var(--zc-text-sub); }
.zc-card-menu-btn:hover { opacity: 1; color: var(--zc-primary); }
.zc-app-menu { background: #fff; border-radius: 6px; box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
               padding: 4px 0; min-width: 148px; }
.zc-app-menu-item { padding: 6px 14px; font-size: 13px; color: var(--zc-text); cursor: pointer;
                    white-space: nowrap; display: flex; align-items: center; gap: 8px; }
.zc-app-menu-item:hover { background: rgba(91, 122, 157, 0.08); }
.zc-app-menu-danger { color: #b96a6a; }
.zc-app-menu-danger:hover { background: #fdf3f2; }
.zc-menu-db { text-decoration: none; }
.zc-modal-warn { margin: 0 0 6px; color: var(--zc-text); font-size: 13px; }
/* 资源监控弹框标题：应用名 + 右侧一枚按状态着色的小徽标 */
.zc-metrics-title { display: inline-flex; align-items: center; gap: 8px; }
.zc-metrics-badge { margin: 0; }

/* ===== 仪表卡：状态轨 / 仪表带（CPU 环 + 近窗曲线 + 字节读数）/ 悬停滑出工具条 ===== */
/* 卡根刻意不设 overflow:hidden——工具条的文字气泡要探出内容框，一刀切的裁剪会把「打开工程目录」
   这种长标签削掉。左侧状态轨自带圆角，不需要靠裁剪对齐。 */
.zc-metric-card { transition: box-shadow 0.2s ease, transform 0.2s ease, border-color 0.2s ease; }
.zc-metric-card::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
                          background: var(--zc-rail, var(--zc-border)); border-radius: 8px 0 0 8px;
                          transition: background 0.25s ease; }
.zc-metric-card:hover { transform: translateY(-2px); border-color: rgba(91, 122, 157, 0.45);
                        box-shadow: 0 8px 24px rgba(47, 58, 71, 0.10); }
.zc-metric-card :deep(.ant-card-body) { padding: 14px 18px 12px; }

/* 仪表带三段：环定宽、曲线吃掉剩余、读数靠右且不被压缩 */
.zc-band { display: grid; grid-template-columns: 72px minmax(0, 1fr) auto; gap: 14px; align-items: center;
           margin: 4px 0 12px; padding: 10px 12px; border: 1px solid var(--zc-border); border-radius: 8px;
           background: linear-gradient(180deg, rgba(91, 122, 157, 0.055), rgba(91, 122, 157, 0.015)); }
.zc-gauge { position: relative; width: 72px; height: 72px; }
.zc-ring { display: block; width: 100%; height: 100%; }
.zc-ring-track { fill: none; stroke: var(--zc-border); stroke-width: 3; }
.zc-ring-fill { fill: none; stroke: var(--zc-primary); stroke-width: 3; stroke-linecap: round;
                transition: stroke-dashoffset 0.5s ease; }
.zc-gauge-body { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
                 justify-content: center; gap: 2px; }
.zc-gauge-value { font-size: 12px; font-weight: 600; line-height: 1; color: var(--zc-text);
                  font-variant-numeric: tabular-nums; white-space: nowrap; }
.zc-gauge-label { font-size: 10px; line-height: 1; letter-spacing: 0.06em; color: var(--zc-text-sub); }
/* 停炉：环退成灰轨，读数只说明「没有采集」，不用 0.0 % 冒充空闲 */
.zc-gauge.off .zc-ring-fill { stroke: var(--zc-border); }
.zc-gauge.off .zc-gauge-value { font-size: 10px; font-weight: 500; color: var(--zc-text-sub); }

.zc-sparks { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.zc-spark { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 8px; align-items: center; }
.zc-spark-label { font-size: 11px; line-height: 1; color: var(--zc-text-sub); white-space: nowrap; }
.zc-spark-svg { display: block; width: 100%; height: 26px; }
.zc-spark-svg path { fill: none; stroke-width: 1.6; stroke-linejoin: round; stroke-linecap: round;
                     /* preserveAspectRatio=none 会把横向拉扁，非缩放描边才不会让 CPU 线比内存线粗 */
                     vector-effect: non-scaling-stroke; }
.zc-spark-cpu .zc-spark-svg path { stroke: #5b7a9d; }
.zc-spark-mem .zc-spark-svg path { stroke: #5b8a72; }
.zc-spark-none { font-size: 11px; color: var(--zc-text-sub); opacity: 0.7; }

.zc-stores { display: flex; flex-direction: column; gap: 5px; font-size: 11px; line-height: 1.2;
             color: var(--zc-text-sub); white-space: nowrap; }
.zc-stores span { display: flex; justify-content: space-between; gap: 10px; }
.zc-stores b { font-weight: 600; color: var(--zc-text); font-variant-numeric: tabular-nums; }
.zc-metric-mem { color: #4a7263; }
.zc-metric-db { color: #9a7333; }
.zc-metric-ws { color: #6f6290; }

/* 工具条常驻：一排图标动作直接摊在卡底。「悬停才出现」已经废掉——看不见就等于不存在。
   overflow 一律 visible，否则文字气泡被裁一半。 */
.zc-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
              margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--zc-border); }
.zc-tb-item { position: relative; display: inline-flex; align-items: center; justify-content: center;
              width: 30px; height: 28px; padding: 0; border: 1px solid var(--zc-border); border-radius: 6px;
              background: #fff; color: var(--zc-text-sub); font-size: 14px; line-height: 1; cursor: pointer;
              transition: color 0.15s, border-color 0.15s, background 0.15s, transform 0.15s; }
.zc-tb-item:hover { color: var(--zc-primary); border-color: var(--zc-primary);
                    background: rgba(91, 122, 157, 0.08); transform: translateY(-1px); }
.zc-tb-item:focus-visible { outline: 2px solid var(--zc-primary); outline-offset: 1px; }
/* 标签：只靠 opacity 隐藏，不进 a11y 树的是「看不见」而非「不存在」——
   它同时是按钮的 accessible name 和悬停气泡的正文，砍掉就等于只剩一堆无名图标。 */
.zc-tb-label { position: absolute; bottom: calc(100% + 6px); left: 50%; z-index: 3;
               padding: 5px 8px; border-radius: 4px; background: rgba(47, 58, 71, 0.92); color: #fff;
               font-size: 12px; line-height: 1; white-space: nowrap; opacity: 0; pointer-events: none;
               transform: translateX(-50%) translateY(2px);
               transition: opacity 0.14s ease, transform 0.14s ease; }
.zc-tb-item:hover .zc-tb-label, .zc-tb-item:focus-visible .zc-tb-label,
.zc-env-btn:hover .zc-tb-label, .zc-env-btn:focus-visible .zc-tb-label {
  opacity: 1; transform: translateX(-50%) translateY(0); }

@media (prefers-reduced-motion: reduce) {
  .zc-metric-card, .zc-metric-card::before, .zc-tb-item, .zc-tb-label,
  .zc-ring-fill, .zc-env-group, .zc-env-btn { transition: none; }
  .zc-metric-card:hover, .zc-tb-item:hover { transform: none; }
  .zc-tb-label { transform: translateX(-50%); }
}
</style>
