// 聊天页应用操作条的反查接缝：workspace_id → app。
// 这里是「整组入口是否渲染」的唯一判据，所以不做任何权限判定——
// GET /api/apps 已按 owner_user_id 过滤，反查不到即等于不该显示。
export function findAppByWorkspace(apps, workspaceId) {
  if (workspaceId == null || !Array.isArray(apps)) return null
  const target = String(workspaceId)
  return apps.find((a) => a.workspace_id != null && String(a.workspace_id) === target) || null
}

// status_dev → 主按钮文案。落库只有 stopped/starting/running/error 四态，
// 未识别取值按 stopped 处理，不留空文案。
export function devActionLabel(statusDev) {
  if (statusDev === 'running') return '重启预览'
  if (statusDev === 'starting') return '启动中…'
  return '启动预览'
}

export function isDevBusy(statusDev) {
  return statusDev === 'starting'
}
