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
