// axios 实例与后端 REST 封装（与 app/api/*.py 路由一一对应）；WS 地址单独导出
import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

// 会话失效统一拦截（Ticket 002 guard）：业务接口 401 → 登录页；
// auth 端点自身的 401（口令错/me 未登录）交给调用方自行处置，不跳转。
// 用 location 而非 import router，避免 api→router→store→api 循环依赖。
http.interceptors.response.use(
  (r) => r,
  (err) => {
    const url = err.config?.url || ''
    if (err.response?.status === 401 && !url.startsWith('/auth/')) {
      window.location.assign('/login')
    }
    return Promise.reject(err)
  },
)

// WS 地址构造：传从 /api 前缀起算的路径（/ws/chat/{id}、/ws/deploy/{id}…，T9 泛化）；
// 不在 /api 前缀下，dev 由 vite proxy 转发到 8100。
export function wsUrl(path) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${path}`
}

export const listWorkspaces = () => http.get('/workspaces')
export const createWorkspace = (data) => http.post('/workspaces', data) // {name?, root_path}
export const deleteWorkspace = (id) => http.delete(`/workspaces/${id}`)
export const browseDir = (path) => http.post('/fs/browse', { path }) // {path?}
export const listSessions = (workspaceId) =>
  http.get('/sessions', { params: { workspace_id: workspaceId } })
export const createSession = (data) => http.post('/sessions', data) // {workspace_id, title?}
export const renameSession = (id, title) => http.patch(`/sessions/${id}`, { title })
export const deleteSession = (id) => http.delete(`/sessions/${id}`)
export const getMessages = (sessionId) => http.get(`/sessions/${sessionId}/messages`)
export const getFileChanges = (sessionId) => http.get(`/sessions/${sessionId}/file-changes`)
export const getConfig = () => http.get('/config')
export const putConfig = (data) => http.put('/config', data)
export const pgStatus = () => http.get('/config/pg-status')
export const testLLM = (data) => http.post('/config/test-llm', data) // {base_url, api_key, model}
export const tree = (workspaceId) => http.get('/fs/tree', { params: { workspace_id: workspaceId } })
export const readFile = (workspaceId, path) =>
  http.get('/files/read', { params: { workspace_id: workspaceId, path } })
export const saveFile = (data) => http.put('/files/save', data) // {workspace_id, path, content}
// 内置「类桌面」文件管理器：单层列目录（相对 posix 路径，根为空串）与调起后端本机文件管理器。
// 下载不走 axios，直接拼 <a href>（见 utils/fsNav.downloadUrl）。
export const listDir = (workspaceId, path) =>
  http.get('/fs/ls', { params: { workspace_id: workspaceId, path: path || '' } })
export const openFolder = (workspaceId) => http.post('/fs/open-folder', { workspace_id: workspaceId })

// ---- 模型库与会话级模型（交互增强） ----
export const listModels = () => http.get('/models')
export const createModel = (data) => http.post('/models', data)
export const updateModel = (id, data) => http.put(`/models/${id}`, data)
export const deleteModel = (id) => http.delete(`/models/${id}`)
export const getSessionDetail = (id) => http.get(`/sessions/${id}`)
export const patchSessionModel = (id, modelConfigId) =>
  http.patch(`/sessions/${id}/model`, { model_config_id: modelConfigId })

// ---- 部署发布套件（应用管理） ----
export const listApps = () => http.get('/apps')
export const createApp = (data) => http.post('/apps', data) // {name, slug, scale, stack}
// 侧栏分组行的两个入口：改名只动显示名（slug 是目录/库/镜像名的锚点，不可改）；
// 删除只删平台记录，工程目录与应用数据库保留（M1 裁定），响应 kept 里带回保留位置。
export const renameApp = (id, name) => http.patch(`/apps/${id}`, { name })
export const deleteApp = (id) => http.delete(`/apps/${id}`)
export const validateAppSlug = (slug) => http.post('/apps/validate-slug', { slug })
export const devStart = (id) => http.post(`/apps/${id}/dev/start`)
export const devStop = (id) => http.post(`/apps/${id}/dev/stop`)
export const devRestart = (id) => http.post(`/apps/${id}/dev/restart`)
// 一键重建 dev 库：drop+provision+从 sqlchange 文件全量 fold（破坏性，202 受理，进度走 WS）
export const devReset = (id) => http.post(`/apps/${id}/dev/reset`)
export const devStatus = (id) => http.get(`/apps/${id}/dev/status`)
export const devLogs = (id, tail = 200, sinceSeq = 0) =>
  http.get(`/apps/${id}/dev/logs`, { params: { tail, since_seq: sinceSeq } })
export const sqlFiles = (id) => http.get(`/apps/${id}/sqlchange/files`)

// 部署事件流 /ws/deploy/{app_id}（dev 由 vite proxy 转发到 8100，与 wsUrl 同前缀规则）
// DeployLogPanel 用此专用函数；M2 的 useDeploy 走泛化 wsUrl('/ws/deploy/{id}')，两者并存。
export function deployWsUrl(appId) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/ws/deploy/${appId}`
}

// ---- 部署发布套件 M2（发布/回滚/prod 生命周期/发布历史/访问地址） ----
export const publishApp = (id, force = false, confirmDestructive = false) =>
  http.post(`/apps/${id}/publish`, { force, confirmDestructive })
export const scaffoldBackfill = (id) => http.post(`/apps/${id}/sqlchange/scaffold-backfill`)
export const rollbackApp = (id, version) => http.post(`/apps/${id}/rollback`, { version })
export const prodStart = (id) => http.post(`/apps/${id}/prod/start`)
export const prodStop = (id) => http.post(`/apps/${id}/prod/stop`)
export const prodRestart = (id) => http.post(`/apps/${id}/prod/restart`)
export const prodStatus = (id) => http.get(`/apps/${id}/prod/status`)
export const appRecords = (id, limit = 50) => http.get(`/apps/${id}/records`, { params: { limit } })
export const deleteRecord = (id, rid) => http.delete(`/apps/${id}/records/${rid}`)
export const appUrls = (id) => http.get(`/apps/${id}/urls`)
// 应用资源监控（Ticket 001）：当前实时值（本期 workspace_size + prod_status）。
export const appMetricsCurrent = (id) => http.get(`/apps/${id}/metrics/current`)
export const systemNics = () => http.get('/system/nics')

// ---- 应用商店 M3-a（上架 / 申请审批）----
// Ticket 008 身份合流：identify/storeMe 已退役，身份一律走 /api/auth/me（authMe）。
export const storeReleases = () => http.get('/store/releases')
export const storeWithdraw = (id) => http.post(`/store/releases/${id}/withdraw`)
export const storeApplications = (box = 'mine') => http.get('/store/applications', { params: { box } })
export const storeMyReleases = () => http.get('/store/applications', { params: { box: 'review' } })
export const storeApply = (releaseId, wantName) =>
  http.post('/store/applications', { release_id: releaseId, want_name: wantName })
export const storeApprove = (id) => http.post(`/store/applications/${id}/approve`, { approve: true })
export const storeReject = (id, reason) =>
  http.post(`/store/applications/${id}/reject`, { approve: false, reason })
export const releaseToStore = (appId, data) => http.post(`/apps/${appId}/release`, data)
export const storeLink = (appId) => http.get(`/store/link/${appId}`)

// ---- 登录体系（Ticket 002 / ADR-0003：actor 即登录用户）----
export const authRegister = (data) => http.post('/auth/register', data) // {nickname, password}
export const authLogin = (data) => http.post('/auth/login', data)
export const authLogout = () => http.post('/auth/logout')
export const authMe = () => http.get('/auth/me')

// ---- 账户级技能库（Ticket 001~003 / ADR-0004）----
export const listSkills = () => http.get('/skills')
export const installSkill = (data) => http.post('/skills/install', data) // {url, subpath?}；多命中返 {candidates}
export const patchSkill = (id, data) => http.patch(`/skills/${id}`, data) // {enabled}
export const deleteSkill = (id) => http.delete(`/skills/${id}`)
