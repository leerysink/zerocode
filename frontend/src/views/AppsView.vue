<script setup>
// 应用页（M1 卡片/向导/预览抽屉 + M2 发布接线）：发布/回滚进度弹层与发布历史抽屉都只在
// 打开时挂载（v-if）——WS 订阅单实例纪律由 PublishModal 自身的 connect/close 保证；
// 卡片 prod 状态靠既有 5s 轮询刷新，正式地址 hover 才懒加载 appUrls（不轰炸后端）。
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  AppstoreOutlined, ClockCircleOutlined, CloudUploadOutlined, CopyOutlined, DashboardOutlined,
  DatabaseOutlined, DeleteOutlined, DisconnectOutlined, EditOutlined,
  EyeOutlined, FolderOpenOutlined, HistoryOutlined, LogoutOutlined, MenuOutlined, MobileOutlined,
  PlayCircleOutlined, PoweroffOutlined, ProfileOutlined, RedoOutlined, ReloadOutlined,
  RocketOutlined, ShareAltOutlined, ShopOutlined,
} from '@ant-design/icons-vue'
import {
  addAppShare, appUrls, appMetricsSummary, approvePublishRequest, authMe, createApp,
  createPublishRequest, deleteApp, devReset, devRestart, devStart, devStatus, devStop,
  getAppShare, leaveAppShare, listApps, listSharedApps, listUsers, openFolder, prodStart,
  prodStop, rejectPublishRequest, releaseToStore, removeAppShare, renameApp, storeAddShot,
  storeLink, storeUninstall, storeUnlink, storeUpgrade, storeFork, validateAppSlug, withdrawPublishRequest,
} from '../api'
import { fmtBytes, fmtPct } from '../utils/metricsFormat'
import PublishHistory from '../components/PublishHistory.vue'
import MetricsModal from '../components/MetricsModal.vue'
import MobileModal from '../components/MobileModal.vue'
import PublishModal from '../components/PublishModal.vue'
import { LARGE_STACKS, SMALL_STACKS, slugFromName, validateForm } from '../utils/appForm'
import { sourcePayload } from '../utils/appSource'
import { copyText } from '../utils/clipboard'
import DeployLogPanel from '../components/DeployLogPanel.vue'
import BackButton from '../components/BackButton.vue'
import { openWorkspaceFilesPage, resolveOpenFolderMode } from '../utils/openFolder'
import { DEFAULT_STORE_CATEGORY } from '../utils/storeCategories'
import { canPublish } from '../utils/publishableStacks'
import { isMobileApp } from '../utils/mobileApp'
import { useConfigStore } from '../stores/config'
import { useCategoryStore } from '../stores/categories'

const apps = ref([])
// 「共享给我」（规格 2026-11-16 §5.3）：独立区块、不混排；页头统计只算自有应用
const sharedApps = ref([])
const loading = ref(true)
const showWizard = ref(false)
const current = ref(0)
const preview = reactive({ open: false, app: null, url: '', status: '', nonce: 0 })
const logs = reactive({ open: false, app: null })
const pub = reactive({ open: false, app: null, mode: 'publish', version: null, autoStart: false, seq: 0 })
const hist = reactive({ open: false, app: null })
const mon = reactive({ open: false, app: null })
const mob = reactive({ open: false, app: null })
const rel = reactive({ open: false, app: null, title: '', summary: '', category: DEFAULT_STORE_CATEGORY, shots: [] })
const urlsCache = reactive({})
// 复制失败时的就地兜底：{ key, url }，key 是失败发生的那一处（`card:<id>` / 'preview'）
const copyFail = reactive({ key: '', url: '' })
const rename = reactive({ open: false, app: null, name: '' })
const del = reactive({ open: false, app: null })
// 共享管理弹框：members 来自 GET share，users 是选人下拉（已剔掉自己，后端也挡自共享）；
// pendingReq 是待审发布申请（S4 审批区块），rejectReason 是驳回理由草稿
const shareM = reactive({ open: false, app: null, members: [], users: [], pick: undefined,
                          pendingReq: null, rejectReason: '', loading: false, busy: false })
const openingFolder = ref(false)
const cfg = useConfigStore()
// 上架弹窗的分类下拉读字典（ADR-0061 §4），不再用写死的镜像数组
const cats = useCategoryStore()
// 仪表带数据：整页一次批量取（appMetricsSummary），按 app_id 贴回卡片。
// window 只认后端回的那个值——曲线口径有两处写法时，前端这处迟早过期。
const msum = reactive({ window: '1h', items: [] })
// 与后端 LIVE_PROD_STATUSES 对齐：停炉后 cpu/memory 没有实时值，画成 0 就是撒谎。
const LIVE_PROD = ['running', 'unhealthy']
const RING_C = 97.39            // r=15.5 的周长（环只画 0-100，超出的部分留给下面的真实数字）
const NO_SAMPLE = '未采集'
const NOT_LIVE = '停炉未采集'
const form = reactive({ name: '', slug: '', slugTouched: false, scale: 'small',
  stack: 'vue3-fastapi', sourceUrl: '', sourceSubpath: '' })
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
  }
  // 共享给我单独取：失败不拖垮自有列表（两块数据互不依赖），静默轮询同样不弹错
  try {
    sharedApps.value = (await listSharedApps()).data
  } catch (e) {
    if (!silent) message.error(e?.response?.data?.detail || '共享应用加载失败')
  } finally {
    if (!silent) loading.value = false
  }
}
// 仪表带的曲线桶宽 150 秒（后端 SUMMARY_WINDOW/SUMMARY_MAX_POINTS 决定），30 秒一轮已经追得上
// 真实变化；状态那一路保持 5 秒静默轮询不变。两条定时分开是刻意的：绑在一起要么曲线白拉，
// 要么状态变钝。
onMounted(() => {
  load()
  cats.load()
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
  { key: 'running', label: '运行中', color: 'var(--zc-success)' },
  { key: 'busy', label: '进行中', color: 'var(--zc-primary)' },
  { key: 'stopped', label: '已停止', color: 'var(--zc-neutral)' },
  { key: 'bad', label: '异常', color: 'var(--zc-danger)' },
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
  // 平均 CPU 只算自有应用：summary 现在也带被共享应用（Ticket 003），页头若不收窄，
  // 「我的应用」的统计会被别人的负载拉偏——这一块的口径始终是「我的」。
  const own = new Set(apps.value.map((a) => a.id))
  const cpus = msum.items.filter((it) => own.has(it.app_id))
    .map((it) => (it.cpu ? Number(it.cpu.value) : null)).filter(Number.isFinite)
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
  // 仓库地址空 / 全空白 → 载荷里根本没有 source 键（不是 source: undefined，下面用条件展开
  // 保证这一点：断言 `'source' in payload` 才钉得住，也不靠 JSON.stringify 顺手丢 undefined）；
  // 超长在前端先拦一次，报错走同一条 message.error，不给后端送一次必然 400 的请求
  let source
  try {
    source = sourcePayload(form.sourceUrl, form.sourceSubpath)
  } catch (e) {
    message.error(e.message)
    return
  }
  try {
    await createApp({ name: form.name.trim(), slug: form.slug, scale: form.scale,
                      stack: form.stack, ...(source ? { source } : {}) })
    message.success('应用已创建')
    showWizard.value = false
    // 这两个字段必须跟着重置：漏一次，第二次创建就静默带上上一个仓库地址、真的又抓一次远程
    Object.assign(form, { name: '', slug: '', slugTouched: false, scale: 'small',
      stack: 'vue3-fastapi', sourceUrl: '', sourceSubpath: '' })
    current.value = 0
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '创建失败')
  }
}

const statusColor = (s) => ({
  running: 'var(--zc-success)', stopped: 'var(--zc-neutral)', starting: 'var(--zc-primary)',
  publishing: 'var(--zc-primary)', error: 'var(--zc-danger)', unhealthy: 'var(--zc-warning)',
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

// ---- 共享管理（规格 2026-11-16 §5.1）：仅自有卡片入口 ----
const shareOptions = computed(() => shareM.users.map((u) => ({
  value: u.id, label: u.is_founder ? `${u.nickname}（创始人）` : u.nickname,
})))
const fmtShareTime = (iso) => (iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '')

async function openShare(a) {
  Object.assign(shareM, { open: true, app: a, members: [], users: [], pick: undefined,
    pendingReq: null, rejectReason: '', loading: true, busy: false })
  try {
    const [mem, users, me] = await Promise.all([getAppShare(a.id), listUsers(), authMe()])
    shareM.members = mem.data?.members || []
    shareM.pendingReq = mem.data?.pending_request || null
    // 自己不进候选：后端对自共享回 400，这里提前把这条路从界面上去掉
    shareM.users = (users.data || []).filter((u) => u.id !== me.data?.id)
  } catch (e) {
    message.error(e?.response?.data?.detail || '共享信息加载失败')
  } finally {
    shareM.loading = false
  }
}
async function refreshMembers() {
  const { data } = await getAppShare(shareM.app.id)
  shareM.members = data?.members || []
  shareM.pendingReq = data?.pending_request || null
}
async function doShare() {
  if (!shareM.app || !shareM.pick) return
  shareM.busy = true
  try {
    await addAppShare(shareM.app.id, shareM.pick)
    message.success('已共享')
    shareM.pick = undefined
    await refreshMembers()
  } catch (e) {
    message.error(e?.response?.data?.detail || '共享失败')
  } finally {
    shareM.busy = false
  }
}
async function doUnshare(m) {
  if (!shareM.app) return
  try {
    await removeAppShare(shareM.app.id, m.user_id)
    message.success(`已移除 ${m.nickname}`)
    await refreshMembers()
  } catch (e) {
    message.error(e?.response?.data?.detail || '移除失败')
  }
}

// ---- 发布申请与审批（规格 §2.3/§5.2，ADR-0050）----
// 被共享者侧：待审时同一个按钮置灰，卡片上给撤回入口；驳回理由常驻卡片直到下一次申请。
function rejectedReason(a) {
  const req = a.publish_request
  return req && req.status === 'rejected' ? (req.reason || '') : ''
}
async function applyPublish(a) {
  try {
    await createPublishRequest(a.id)
    message.success('已提交发布申请，等待所有者审批')
    await load(true)
  } catch (e) {
    message.error(e?.response?.data?.detail || '申请发布失败')
  }
}
async function withdrawPublish(a) {
  if (!a.publish_request?.id) return
  try {
    await withdrawPublishRequest(a.id, a.publish_request.id)
    message.success('已撤回发布申请')
    await load(true)
  } catch (e) {
    message.error(e?.response?.data?.detail || '撤回失败')
  }
}
// 退出共享（S13）：只删自己那一行 app_share，不删会话/文件/发布记录；确认后卡片随刷新消失。
// 所有者重新共享即恢复原样，所以确认文案不写成「永久移除」。
function askLeaveShare(a) {
  Modal.confirm({
    title: `退出共享「${a.name}」？`,
    content: '退出后该应用将从「共享给我」消失；所有者重新共享即可恢复可见。',
    okText: '退出共享', okType: 'danger', cancelText: '取消',
    onOk: async () => {
      try {
        await leaveAppShare(a.id)
        message.success('已退出共享')
        await load(true)
      } catch (e) {
        message.error(e?.response?.data?.detail || '退出失败')
      }
    },
  })
}
// 所有者侧审批：批准即执行（后端先跑与直接发布同一道闸门）；chat_active 的 409 与直接发布
// 同形状，按 code 弹强制确认后带 force 重试。
async function doApprove(force = false) {
  if (!shareM.app || !shareM.pendingReq) return
  shareM.busy = true
  try {
    await approvePublishRequest(shareM.app.id, shareM.pendingReq.id, force)
    message.success('已批准，正在发布新版本')
    await refreshMembers()
    await load(true)
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409 && detail && detail.code === 'chat_active') {
      Modal.confirm({
        title: 'AI 正在修改代码', content: detail.message,
        okText: '继续发布', cancelText: '等一等',
        onOk: () => doApprove(true),
      })
    } else {
      message.error(typeof detail === 'string' ? detail : '审批未受理')
    }
  } finally {
    shareM.busy = false
  }
}
async function doReject() {
  const reason = shareM.rejectReason.trim()
  if (!shareM.app || !shareM.pendingReq || !reason) return
  shareM.busy = true
  try {
    await rejectPublishRequest(shareM.app.id, shareM.pendingReq.id, reason)
    message.success('已驳回')
    shareM.rejectReason = ''
    await refreshMembers()
    await load(true)
  } catch (e) {
    message.error(e?.response?.data?.detail || '驳回失败')
  } finally {
    shareM.busy = false
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
  Object.assign(rel, { app: a, title: a.name, summary: '', category: DEFAULT_STORE_CATEGORY, shots: [], open: true })
}
function onPickRelShots(e) { rel.shots = Array.from(e.target.files || []) }
async function doRelease() {
  if (!rel.app) return
  try {
    const created = await releaseToStore(rel.app.id, { title: rel.title.trim() || rel.app.name, summary: rel.summary, category: rel.category })
    for (const f of rel.shots) await storeAddShot(created.data.id, f)
    message.success('已上架商店，可在侧栏「商店」页看到')
    rel.open = false
    await load()
  } catch (e) {
    const d = e?.response?.data?.detail
    message.error(typeof d === 'string' ? d : '上架失败')
  }
}

// ---------- 011 商店实例手动升级（M3-a §6.4）：取 link 判可升级 → 确认 → 202 受理 ----------
async function openUpgrade(a) {
  let info
  try {
    info = (await storeLink(a.id)).data
  } catch (e) {
    message.error('取升级信息失败')
    return
  }
  if (!info.upgradable) {
    if (info.channel === 'detached') { fork.app = a; fork.open = true }
    else message.info('已是最新版本')
    return
  }
  Modal.confirm({
    title: `升级「${a.name}」到 ${info.latest_release.version}？`,
    content: '升级会先在数据克隆上预演，再切换程序；升级中会有几秒不可访问。失败会自动退回旧版程序。',
    okText: '升级', cancelText: '取消',
    onOk: async () => {
      try {
        await storeUpgrade(a.id, info.latest_release.id)
        message.success('升级已受理，进度见查看日志')
        await load()
      } catch (e) {
        const d = e?.response?.data?.detail
        message.error(typeof d === 'string' ? d : '升级未受理')
      }
    },
  })
}

// ---------- 012 fork 问人（M3-a §6.5）：保留定制 / 丢弃定制回官方 ----------
const fork = reactive({ open: false, app: null })
async function doFork(choice) {
  if (!fork.app) return
  try {
    await storeFork(fork.app.id, choice)
    message.success(choice === 'keep_custom' ? '已保留定制，不再接收升级' : '已回到官方版，数据保留')
    fork.open = false
    await load()
  } catch (e) {
    const d = e?.response?.data?.detail
    message.error(typeof d === 'string' ? d : '操作失败')
  }
}

// ---------- 007/008 商店实例退出：解绑（保留应用）/ 卸载（全删，高危） ----------
function openUnlink(a) {
  Modal.confirm({
    title: `解绑商店实例「${a.name}」？`,
    content: '断开与商店版本的绑定：不再接收升级，商店角标消失。容器、数据库、源码与会话全部保留，应用转为普通自有应用。',
    okText: '解绑', okType: 'danger', cancelText: '取消',
    onOk: async () => {
      try {
        await storeUnlink(a.id)
        message.success('已解绑，应用转为普通自有应用')
        await load()
      } catch (e) {
        const d = e?.response?.data?.detail
        message.error(typeof d === 'string' ? d : '解绑失败')
      }
    },
  })
}
function openUninstall(a) {
  Modal.confirm({
    title: `卸载商店实例「${a.name}」？`,
    content: '将停止预览与正式环境、删除两个数据库（dev/prod）、删除源码目录、删除会话记录。此操作不可恢复。',
    okText: '卸载', okType: 'danger', cancelText: '取消',
    onOk: async () => {
      try {
        const res = await storeUninstall(a.id)
        if (res.data?.warnings?.length) message.warning('卸载完成，停栈有告警：' + res.data.warnings.join('；'))
        else message.success('已卸载')
        await load()
      } catch (e) {
        const d = e?.response?.data?.detail
        message.error(typeof d === 'string' ? d : '卸载失败')
      }
    },
  })
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
    <template v-else>
    <a-empty v-if="!apps.length && !sharedApps.length"
             description="还没有应用——点「新建应用」，用一句话描述你要的软件"
             style="margin-top: 80px" />
    <div v-if="apps.length" class="zc-grid">
      <a-card v-for="a in apps" :key="a.id" class="zc-card zc-metric-card"
              :style="{ '--zc-rail': statusColor(a.status_prod) }">
        <template #title>
          <span class="zc-dot" :style="{ background: statusColor(a.status_dev) }" />
          <span class="zc-card-name">{{ a.name }}</span>
          <a-tag v-if="a.store" class="zc-store-tag"
                 :color="a.store.channel === 'detached' ? 'orange' : undefined">
            {{ a.store.channel === 'detached' ? '已定制' : `来自商店 ${a.store.release_version || ''}` }}
          </a-tag>
          <a-tag class="zc-card-tag">{{ a.scale === 'large' ? '大型' : '小型' }} · {{ a.stack }}</a-tag>
        </template>
        <template #extra>
          <!-- S3 待审角标：有 pending 发布申请就在 ☰ 上点一颗红点；随 5s 轮询消失于批准/驳回/撤回之后 -->
          <a-badge :dot="a.pending_publish" class="zc-pending-badge"
                   :title="a.pending_publish ? '有发布申请待审批' : ''">
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <menu-outlined class="zc-card-menu-btn" title="破坏性操作" @click.stop />
              <template #overlay>
                <div class="zc-app-menu">
                  <!-- ☰ 收着共享管理与两个会删数据/删应用的动作。平铺进工具条等于把误点的代价摊到每次
                       点击上，而它们本来各自带一次确认框，收在这里是对的。 -->
                  <div class="zc-app-menu-item zc-menu-share" @click.stop="openShare(a)">
                    <share-alt-outlined /> 共享管理
                  </div>
                  <div v-if="a.store" class="zc-app-menu-item zc-menu-unlink" @click.stop="openUnlink(a)">
                    <disconnect-outlined /> 解绑商店
                  </div>
                  <div v-if="a.store" class="zc-app-menu-item zc-app-menu-danger zc-menu-uninstall"
                       @click.stop="openUninstall(a)">
                    <delete-outlined /> 卸载实例
                  </div>
                  <div class="zc-app-menu-item zc-menu-reset" @click.stop="resetDev(a)"><redo-outlined /> 重建 dev</div>
                  <div class="zc-app-menu-item zc-app-menu-danger zc-menu-del" @click.stop="openDelete(a)">
                    <delete-outlined /> 删除应用
                  </div>
                </div>
              </template>
            </a-dropdown>
          </a-badge>
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
          <button v-if="a.store" type="button"
                  class="zc-tb-item zc-menu-upgrade" @click.stop="openUpgrade(a)">
            <cloud-upload-outlined /><span class="zc-tb-label">升级</span></button>
        </div>
      </a-card>
    </div>

    <!-- 「共享给我」（规格 2026-11-16 §5.3/§5.6）：独立区块、不混排；为空时整块不渲染。
         动作按被共享者权限裁剪：可起停/重启 dev、预览、看观测与历史；破坏性动作与发布留在所有者卡片。 -->
    <section v-if="sharedApps.length" class="zc-shared" aria-label="共享给我">
      <h3 class="zc-shared-title">
        <share-alt-outlined /> 共享给我
        <span class="zc-shared-count">{{ sharedApps.length }}</span>
      </h3>
      <div class="zc-grid">
        <a-card v-for="a in sharedApps" :key="`shared-${a.id}`" class="zc-card zc-shared-card"
                :style="{ '--zc-rail': statusColor(a.status_prod) }">
          <template #title>
            <span class="zc-dot" :style="{ background: statusColor(a.status_dev) }" />
            <span class="zc-card-name">{{ a.name }}</span>
            <a-tag class="zc-share-owner-tag">所有者：{{ a.owner_nickname }}</a-tag>
            <a-tag class="zc-card-tag">{{ a.scale === 'large' ? '大型' : '小型' }} · {{ a.stack }}</a-tag>
          </template>
          <template #extra>
            <!-- 共享卡 ☰ 只有「退出共享」一个动作（S13）：协作方唯一的离开方式，不删任何数据 -->
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <menu-outlined class="zc-card-menu-btn" title="共享操作" @click.stop />
              <template #overlay>
                <div class="zc-app-menu">
                  <div class="zc-app-menu-item zc-app-menu-danger zc-menu-leave"
                       @click.stop="askLeaveShare(a)"><logout-outlined /> 退出共享</div>
                </div>
              </template>
            </a-dropdown>
          </template>
          <p class="zc-line">
            预览：{{ statusText(a.status_dev) }} · 端口 {{ a.dev_port }}
            <a v-if="a.status_dev === 'running'" :href="a.preview_url" target="_blank">打开</a>
          </p>
          <p class="zc-line sub">
            正式：<span class="zc-dot" :style="{ background: statusColor(a.status_prod) }" />{{ statusText(a.status_prod) }}
            <template v-if="a.current_version"> · {{ a.current_version }}</template>
          </p>
          <p v-if="copyFail.key === `card:${a.id}`" class="zc-line zc-copy-manual">{{ copyFail.url }}</p>
          <!-- 环境胶囊：dev 自助起停（003）；prod 起停/重启也开放给协作方（004），但发布仍走所有者/申请 -->
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
              <!-- 被共享者没有直接发布权（ADR-0049）：同一位置放「申请发布」，所有者批准即执行（ADR-0050） -->
              <template v-if="a.publish_request?.status === 'pending'">
                <button type="button" class="zc-env-btn zc-env-btn-prim" disabled
                        aria-label="待审批" title="已提交发布申请，等待所有者审批">
                  <clock-circle-outlined /><span class="zc-tb-label">待审批</span>
                </button>
                <button type="button" class="zc-env-btn zc-act-withdraw" aria-label="撤回申请"
                        title="撤回发布申请" @click="withdrawPublish(a)">
                  <delete-outlined /><span class="zc-tb-label">撤回申请</span>
                </button>
              </template>
              <button v-else type="button" class="zc-env-btn zc-env-btn-prim zc-act-apply"
                      :disabled="!canPublish(a) || a.status_prod === 'publishing'"
                      :aria-label="a.publish_request?.status === 'rejected' ? '重新申请发布' : '申请发布'"
                      :title="!canPublish(a) ? '该栈暂不支持发布'
                        : (a.status_prod === 'publishing' ? '应用正在发布中' : '申请发布新版本（需所有者批准）')"
                      @click="applyPublish(a)">
                <cloud-upload-outlined /><span class="zc-tb-label">{{
                  a.publish_request?.status === 'rejected' ? '重新申请' : '申请发布' }}</span>
              </button>
              <button type="button" class="zc-env-btn" :aria-label="prodToggleLabel(a)" @click="toggleProd(a)">
                <poweroff-outlined v-if="a.status_prod === 'running'" />
                <play-circle-outlined v-else /><span class="zc-tb-label">{{ prodToggleLabel(a) }}</span>
              </button>
            </div>
          </div>
          <!-- 驳回理由常驻卡片直到下一次申请：toast 刷新就没了，理由必须留在原地 -->
          <p v-if="rejectedReason(a)" class="zc-line zc-reject-reason">
            发布申请被驳回：{{ rejectedReason(a) }}
          </p>
          <div class="zc-toolbar" role="group" aria-label="只读操作">
            <button type="button" class="zc-tb-item zc-menu-preview" @click.stop="openPreview(a)">
              <eye-outlined /><span class="zc-tb-label">预览</span></button>
            <button type="button" class="zc-tb-item zc-menu-copy"
                    @click.stop="copyUrl(a.preview_url, `card:${a.id}`)">
              <copy-outlined /><span class="zc-tb-label">复制预览地址</span></button>
            <button type="button" class="zc-tb-item zc-menu-metrics" @click.stop="openMetrics(a)">
              <dashboard-outlined /><span class="zc-tb-label">资源监控</span></button>
            <button type="button" class="zc-tb-item zc-menu-history" @click.stop="openHistory(a)">
              <history-outlined /><span class="zc-tb-label">发布历史</span></button>
            <button type="button" class="zc-tb-item zc-menu-logs" @click.stop="openLogs(a)">
              <profile-outlined /><span class="zc-tb-label">查看日志</span></button>
            <!-- 只读工程文件走网页工作台（S9）：协作方没有「打开工程目录」的系统入口 -->
            <button type="button" class="zc-tb-item zc-menu-files" @click.stop="openFilesPage(a)">
              <folder-open-outlined /><span class="zc-tb-label">工程文件</span></button>
            <a class="zc-tb-item zc-menu-db" :href="`/apps/${a.id}/db`" target="_blank" @click.stop>
              <database-outlined /><span class="zc-tb-label">DB 操作</span></a>
            <button v-if="isMobileApp(a)" type="button" class="zc-tb-item zc-menu-mobile"
                    @click.stop="openMobile(a)">
              <mobile-outlined /><span class="zc-tb-label">移动端</span></button>
          </div>
        </a-card>
      </div>
    </section>
    </template>

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
          <a-form-item label="仓库地址"
                       help="可选：Gitee 公开仓库地址，创建时把仓库文本内容烘成快照，站点可从 /source/ 读">
            <a-input v-model:value="form.sourceUrl" allow-clear
                     placeholder="https://gitee.com/owner/repo 或 …/tree/master/docs" />
          </a-form-item>
          <a-form-item label="子目录"
                       help="可选：只收这个目录下的正文；留空则沿用地址里 /tree/&lt;分支&gt;/&lt;目录&gt;，地址不带目录时才全域扫描">
            <a-input v-model:value="form.sourceSubpath" allow-clear placeholder="如 docs" />
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

    <!-- 共享管理（规格 2026-11-16 §5.1）：成员列表 + 选人共享 + 移除 + 待审发布申请 -->
    <a-modal v-model:open="shareM.open" :title="`共享管理 · ${shareM.app?.name || ''}`"
             :footer="null" width="520px" destroy-on-close>
      <div class="zc-share-add">
        <a-select v-model:value="shareM.pick" show-search :options="shareOptions"
                  placeholder="选择一个账号共享给 TA" style="flex: 1" />
        <a-button type="primary" :disabled="!shareM.pick" :loading="shareM.busy"
                  @click="doShare">共享</a-button>
      </div>
      <!-- 待审区块（S4）：申请人 + 时间 + 批准/驳回。批准即执行，驳回必须填理由 -->
      <div v-if="shareM.pendingReq" class="zc-share-pending">
        <p class="zc-share-pending-head">
          <clock-circle-outlined />
          <b class="zc-share-pending-nick">{{ shareM.pendingReq.requester_nickname }}</b>
          申请发布新版本
          <span class="zc-share-pending-time">{{ fmtShareTime(shareM.pendingReq.created_at) }}</span>
        </p>
        <div class="zc-share-pending-acts">
          <a-input v-model:value="shareM.rejectReason" size="small" placeholder="驳回理由（必填）"
                   class="zc-share-reason" />
          <a-button size="small" danger :disabled="!shareM.rejectReason.trim()" :loading="shareM.busy"
                    @click="doReject">驳回</a-button>
          <a-button size="small" type="primary" :loading="shareM.busy" @click="doApprove()">批准并发布</a-button>
        </div>
      </div>
      <a-spin v-if="shareM.loading" />
      <a-empty v-else-if="!shareM.members.length" description="还没有共享给任何人" />
      <ul v-else class="zc-share-members">
        <li v-for="m in shareM.members" :key="m.user_id" class="zc-share-member">
          <span class="zc-share-nick">{{ m.nickname }}</span>
          <span class="zc-share-time">{{ fmtShareTime(m.created_at) }}</span>
          <a-button size="small" type="text" danger @click="doUnshare(m)">移除</a-button>
        </li>
      </ul>
      <p class="zc-modal-tip">共享长期有效，可随时移除；被共享者不能删除或重命名该应用。</p>
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
              style="width: 100%; height: calc(100vh - 160px); border: 1px solid var(--zc-border-alt); border-radius: 4px" />
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
      <a-select v-model:value="rel.category" style="width: 100%; margin-top: 8px">
        <a-select-option v-for="c in cats.enabledKeys" :key="c" :value="c">{{ c }}</a-select-option>
      </a-select>
      <a-textarea v-model:value="rel.summary" placeholder="一句话简介（可选）" :rows="3"
                  style="margin-top: 8px" />
      <input data-test="rel-shots" type="file" accept="image/png,image/jpeg" multiple
             style="margin-top: 8px" @change="onPickRelShots" />
      <p class="zc-modal-tip">可选上传至多 5 张截图（PNG/JPEG，单张不超过 2MB）。</p>
      <p class="zc-modal-tip">
        上架的是当前正式在服版本 {{ rel.app?.current_version ? `（${rel.app.current_version}）` : '' }}，
        含真实执行过的数据变更序列；获批者会得到独立代码与独立数据库的实例。
      </p>
    </a-modal>

    <a-modal v-model:open="fork.open" title="实例已定制" :footer="null" destroy-on-close>
      <p class="zc-modal-tip">该实例的代码或数据序列已偏离官方版本，无法直接升级。请选择：</p>
      <a-space direction="vertical" style="width: 100%">
        <a-button data-test="fork-keep" block @click="doFork('keep_custom')">
          保留我的定制（不再升级）
        </a-button>
        <a-button data-test="fork-reset" block danger @click="doFork('reset_to_official')">
          丢弃定制，回到官方版（保留数据）
        </a-button>
      </a-space>
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
           background: linear-gradient(105deg, rgba(var(--zc-primary-rgb), 0.13), rgba(var(--zc-primary-rgb), 0.02) 58%,
                                         rgba(var(--zc-amber-rgb), 0.05)); }
.zc-head { display: flex; justify-content: space-between; align-items: center; }
.zc-head-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.zc-head h2 { margin: 0; font-size: 22px; font-weight: 600; color: var(--zc-text); white-space: nowrap; }
.zc-hero-count { flex-shrink: 0; padding: 2px 9px; border-radius: 10px; font-size: 12px; font-weight: 500;
                 line-height: 1.5; color: var(--zc-primary); background: rgba(var(--zc-primary-rgb), 0.12);
                 font-variant-numeric: tabular-nums; }
/* 返回做成描边 chip：住在渐变里时，裸文字链接的存在感比不过 22px 标题 */
.zc-hero .zc-back { padding: 2px 10px 2px 7px; border: 1px solid var(--zc-border); border-radius: 14px;
                    background: var(--zc-bg-card); }
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
                  word-break: break-all; background: rgba(var(--zc-primary-rgb), 0.08); border-radius: 4px; padding: 2px 6px; }
/* 环境动作行：两段各一颗胶囊——状态点 + 字母名 + 两个图标动作，整条约 250px，稳稳落在一行。
   分组靠胶囊外壳，不再靠中间竖线；中文全称退到 title 与按钮气泡。 */
.zc-acts { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.zc-env-group { display: inline-flex; align-items: center; gap: 4px; height: 30px; padding: 0 5px 0 10px;
                border: 1px solid var(--zc-border); border-radius: 15px; background: var(--zc-bg-card);
                transition: border-color 0.15s, box-shadow 0.15s; }
.zc-env-group:hover { box-shadow: 0 1px 5px rgba(var(--zc-shadow-rgb), 0.08); }
.zc-env-dev { border-color: rgba(var(--zc-primary-rgb), 0.30); }
.zc-env-prod { border-color: rgba(var(--zc-amber-rgb), 0.34); }
.zc-env-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.zc-env-name { margin-inline-end: 2px; font-size: 11px; font-weight: 600; line-height: 1; letter-spacing: 0.04em; }
.zc-env-dev .zc-env-name { color: var(--zc-primary); }
.zc-env-prod .zc-env-name { color: var(--zc-warning-text); }
.zc-env-btn { position: relative; display: inline-flex; align-items: center; justify-content: center;
              width: 27px; height: 26px; padding: 0; border: 1px solid transparent; border-radius: 7px;
              background: none; color: var(--zc-text-sub); font-size: 14px; line-height: 1; cursor: pointer;
              transition: color 0.15s, background 0.15s, border-color 0.15s; }
.zc-env-btn:hover { color: var(--zc-primary); background: rgba(var(--zc-primary-rgb), 0.10); }
.zc-env-btn:focus-visible { outline: 2px solid var(--zc-primary); outline-offset: 1px; }
.zc-env-btn:disabled { color: var(--zc-text-sub); cursor: not-allowed; opacity: 0.45; }
/* 发布是这段里唯一有后果的动作，常驻主色（置灰时由 :disabled 接管） */
.zc-env-btn-prim { color: var(--zc-primary); }
.zc-spin { display: block; margin: 60px auto; }
.zc-pick { border: 1px solid var(--zc-border); border-radius: 6px; padding: 10px 14px; margin-bottom: 10px;
           cursor: pointer; display: flex; flex-direction: column; gap: 4px; }
.zc-pick.on { border-color: var(--zc-primary); background: rgba(var(--zc-primary-rgb), 0.08); }
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
.zc-app-menu { background: var(--zc-bg-card); border-radius: 6px; box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
               padding: 4px 0; min-width: 148px; }
.zc-app-menu-item { padding: 6px 14px; font-size: 13px; color: var(--zc-text); cursor: pointer;
                    white-space: nowrap; display: flex; align-items: center; gap: 8px; }
.zc-app-menu-item:hover { background: rgba(var(--zc-primary-rgb), 0.08); }
.zc-app-menu-danger { color: var(--zc-danger); }
.zc-app-menu-danger:hover { background: var(--zc-danger-bg-alt); }
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
.zc-metric-card:hover { transform: translateY(-2px); border-color: rgba(var(--zc-primary-rgb), 0.45);
                        box-shadow: 0 8px 24px rgba(var(--zc-shadow-rgb), 0.10); }
.zc-metric-card :deep(.ant-card-body) { padding: 14px 18px 12px; }

/* 仪表带三段：环定宽、曲线吃掉剩余、读数靠右且不被压缩 */
.zc-band { display: grid; grid-template-columns: 72px minmax(0, 1fr) auto; gap: 14px; align-items: center;
           margin: 4px 0 12px; padding: 10px 12px; border: 1px solid var(--zc-border); border-radius: 8px;
           background: linear-gradient(180deg, rgba(var(--zc-primary-rgb), 0.055), rgba(var(--zc-primary-rgb), 0.015)); }
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
.zc-spark-cpu .zc-spark-svg path { stroke: var(--zc-primary); }
.zc-spark-mem .zc-spark-svg path { stroke: var(--zc-success); }
.zc-spark-none { font-size: 11px; color: var(--zc-text-sub); opacity: 0.7; }

.zc-stores { display: flex; flex-direction: column; gap: 5px; font-size: 11px; line-height: 1.2;
             color: var(--zc-text-sub); white-space: nowrap; }
.zc-stores span { display: flex; justify-content: space-between; gap: 10px; }
.zc-stores b { font-weight: 600; color: var(--zc-text); font-variant-numeric: tabular-nums; }
.zc-metric-mem { color: var(--zc-success-deep); }
.zc-metric-db { color: var(--zc-warning-readout); }
.zc-metric-ws { color: var(--zc-purple-deep); }

/* 工具条常驻：一排图标动作直接摊在卡底。「悬停才出现」已经废掉——看不见就等于不存在。
   overflow 一律 visible，否则文字气泡被裁一半。 */
.zc-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
              margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--zc-border); }
.zc-tb-item { position: relative; display: inline-flex; align-items: center; justify-content: center;
              width: 30px; height: 28px; padding: 0; border: 1px solid var(--zc-border); border-radius: 6px;
              background: var(--zc-bg-card); color: var(--zc-text-sub); font-size: 14px; line-height: 1; cursor: pointer;
              transition: color 0.15s, border-color 0.15s, background 0.15s, transform 0.15s; }
.zc-tb-item:hover { color: var(--zc-primary); border-color: var(--zc-primary);
                    background: rgba(var(--zc-primary-rgb), 0.08); transform: translateY(-1px); }
.zc-tb-item:focus-visible { outline: 2px solid var(--zc-primary); outline-offset: 1px; }
/* 标签：只靠 opacity 隐藏，不进 a11y 树的是「看不见」而非「不存在」——
   它同时是按钮的 accessible name 和悬停气泡的正文，砍掉就等于只剩一堆无名图标。 */
.zc-tb-label { position: absolute; bottom: calc(100% + 6px); left: 50%; z-index: 3;
               padding: 5px 8px; border-radius: 4px; background: var(--zc-tooltip-bg); color: var(--zc-tooltip-text);
               font-size: 12px; line-height: 1; white-space: nowrap; opacity: 0; pointer-events: none;
               transform: translateX(-50%) translateY(2px);
               transition: opacity 0.14s ease, transform 0.14s ease; }
.zc-tb-item:hover .zc-tb-label, .zc-tb-item:focus-visible .zc-tb-label,
.zc-env-btn:hover .zc-tb-label, .zc-env-btn:focus-visible .zc-tb-label {
  opacity: 1; transform: translateX(-50%) translateY(0); }

/* ===== 共享（规格 2026-11-16）：共享给我独立区块 + 共享管理弹框 ===== */
.zc-shared { margin-top: 30px; }
.zc-shared-title { display: flex; align-items: center; gap: 8px; margin: 0 0 14px;
                   font-size: 15px; font-weight: 600; color: var(--zc-text); }
.zc-shared-count { min-width: 20px; padding: 0 7px; border-radius: 10px;
                   background: rgba(var(--zc-primary-rgb), 0.12); color: var(--zc-primary);
                   font-size: 12px; font-weight: 600; line-height: 20px; text-align: center; }
.zc-share-owner-tag { margin-inline-start: 8px; font-weight: 400; vertical-align: middle;
                      color: var(--zc-primary); background: rgba(var(--zc-primary-rgb), 0.08);
                      border-color: rgba(var(--zc-primary-rgb), 0.30); }
.zc-share-add { display: flex; gap: 10px; margin: 4px 0 16px; }
.zc-share-members { list-style: none; margin: 0; padding: 0; border: 1px solid var(--zc-border);
                    border-radius: 8px; overflow: hidden; }
.zc-share-member { display: flex; align-items: center; gap: 12px; padding: 8px 12px; }
.zc-share-member + .zc-share-member { border-top: 1px solid var(--zc-border); }
.zc-share-nick { font-size: 13px; font-weight: 600; color: var(--zc-text); }
.zc-share-time { flex: 1; font-size: 12px; color: var(--zc-text-sub); }

/* ===== 发布申请与审批（ADR-0050）===== */
/* 待审角标：红点挂在 ☰ 上；批准/驳回/撤回后随 5s 轮询自灭，不靠额外接口 */
.zc-pending-badge { display: inline-flex; }
/* 被共享者卡上的驳回理由行：常驻到下一次申请，toast 刷新就没了的理由不算数 */
.zc-reject-reason { margin: 6px 0 0; padding: 4px 8px; border-radius: 4px; font-size: 12px;
                    color: var(--zc-danger); background: var(--zc-danger-bg-alt); }
/* 共享管理弹框的待审区块：申请人 + 时间 + 驳回理由输入 + 两个决策动作 */
.zc-share-pending { margin: 0 0 16px; padding: 10px 12px; border: 1px solid rgba(var(--zc-amber-rgb), 0.34);
                    border-radius: 8px; background: rgba(var(--zc-amber-rgb), 0.06); }
.zc-share-pending-head { display: flex; align-items: center; gap: 6px; margin: 0;
                         font-size: 13px; color: var(--zc-text); }
.zc-share-pending-nick { font-weight: 600; }
.zc-share-pending-time { margin-inline-start: auto; font-size: 12px; color: var(--zc-text-sub); }
.zc-share-pending-acts { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.zc-share-reason { flex: 1; }

@media (prefers-reduced-motion: reduce) {
  .zc-metric-card, .zc-metric-card::before, .zc-tb-item, .zc-tb-label,
  .zc-ring-fill, .zc-env-group, .zc-env-btn { transition: none; }
  .zc-metric-card:hover, .zc-tb-item:hover { transform: none; }
  .zc-tb-label { transform: translateX(-50%); }
}
</style>
