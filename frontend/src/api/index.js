// axios 实例与后端 REST 封装（与 app/api/*.py 路由一一对应）；WS 地址单独导出
import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

// WS 聊天端点 /ws/chat/{session_id}（不在 /api 前缀下，dev 由 vite proxy 转发到 8100）
export function wsUrl(sessionId) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/ws/chat/${sessionId}`
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
export const devStatus = (id) => http.get(`/apps/${id}/dev/status`)
export const sqlFiles = (id) => http.get(`/apps/${id}/sqlchange/files`)
