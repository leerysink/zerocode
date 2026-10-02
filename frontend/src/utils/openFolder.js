// 「打开文件夹」方式判定：默认走内置文件管理器，只有显式 auto 才试着按访问地址直开。
// 后端跑在哪台机器上都能开自己的文件夹，但用户看不见——所以「同机」只能由浏览器的访问地址推断：
// 通过回环地址访问 = 浏览器与后端同机 = explorer 的窗口会开在用户眼前。
// 内置那一路是个独立页面（新标签打开 /workspaces/{id}/files），所以地址也在这里拼：
// 入口与路由表各写一份地址迟早会静默断链，收在一处由 router/index.test.js 对拍。
// 抽成纯函数是为了把 三种取值 × 各种访问地址 的组合全部钉死（见 openFolder.test.js），不必挂组件。

// fs.open_folder_mode 的合法取值（SettingsView 下拉与此一一对应）
export const OPEN_FOLDER_MODE_AUTO = 'auto'
export const OPEN_FOLDER_MODE_SYSTEM = 'system'
export const OPEN_FOLDER_MODE_WEB = 'web'

// 回环形态：localhost / 127/8 整段 / IPv6 ::1（浏览器带方括号），忽略大小写与 FQDN 尾点
export function isLocalHostname(hostname) {
  const h = String(hostname || '').toLowerCase().replace(/\.$/, '')
  if (!h) return false
  if (h === 'localhost' || h === '::1' || h === '[::1]') return true
  return /^127\./.test(h)
}

// 返回实际执行方式：'system' 调起系统文件管理器，'web' 打开内置文件管理器。
// system / web 是人工强制覆盖（远端部署在本机浏览器、或容器里没图形会话时用）；
// auto 才按访问地址分流；其余取值（未配置、脏值、大小写不符）一律走 web：
// 默认值只在 stores/config.js 与 changelog 播种处落一次（ADR-0009），不在这里另立一个 auto 兜底。
export function resolveOpenFolderMode(mode, hostname) {
  if (mode === OPEN_FOLDER_MODE_AUTO) return isLocalHostname(hostname) ? OPEN_FOLDER_MODE_SYSTEM : OPEN_FOLDER_MODE_WEB
  if (mode === OPEN_FOLDER_MODE_SYSTEM) return OPEN_FOLDER_MODE_SYSTEM
  return OPEN_FOLDER_MODE_WEB
}

// 工作区 id 只认纯数字字符串。id 全链路按字符串走（BIGSERIAL 将来换雪花 ID 就不许 Number()），
// 脏值一律判成「没有地址」：宁可不打开标签，也不开一个必然 404 的页面。
export function normalizeWorkspaceId(id) {
  const s = String(id ?? '')
  return /^\d+$/.test(s) ? s : ''
}

// 内置文件管理器整页地址；id 不合法返回空串。
export function workspaceFilesPath(id) {
  const ws = normalizeWorkspaceId(id)
  return ws ? `/workspaces/${ws}/files` : ''
}

// 新标签打开整页。名字进 query 是让页面顶栏直接有名字，不必为标题再请求一次工作区；
// 必须 encodeURIComponent：'#' 会掉进 fragment、'&' 会被拆成多参数，中文名还得防编码歧义。
// 窗口名按工作区固定，同一个工作区反复点复用那一个标签，不会叠出一排。
// 返回 false 表示没开成（地址不合法、不在浏览器里，或被弹窗拦截），由调用方去提示。
// 写 globalThis.open 不写 window.open：浏览器里两者是同一个函数，而这模块本身是无 DOM 依赖的纯函数（单测跑在 node）。
export function openWorkspaceFilesPage(id, name) {
  const ws = normalizeWorkspaceId(id)
  if (!ws) return false
  const label = String(name ?? '')
  const base = workspaceFilesPath(ws)
  const url = label ? `${base}?name=${encodeURIComponent(label)}` : base
  return Boolean(globalThis.open?.(url, `zerocode-files-${ws}`))
}
