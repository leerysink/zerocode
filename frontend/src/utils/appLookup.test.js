// 聊天页应用操作条的反查接缝（spec §5.2 / 票 001）。
// 权限不在这里判：GET /api/apps 已按 owner_user_id 过滤（app_service.py::list_apps 的 owner 过滤），
// 反查不到即等于「不是你的应用 / 没有应用 / 还没绑工作区」，一个判据管三件事。
import { describe, it, expect } from 'vitest'
import { findAppByWorkspace, devActionLabel, isDevBusy } from './appLookup'

const apps = [
  { id: 11, workspace_id: 101, name: 'windows 模拟生成', slug: 'win', preview_url: 'http://localhost:8211/' },
  { id: 12, workspace_id: 102, name: 'test1', slug: 'test1', preview_url: 'http://localhost:8213/' },
]

it('按 workspace_id 反查到该工作区所属的应用', () => {
  expect(findAppByWorkspace(apps, 102)).toBe(apps[1])
})

// 工作区 id 全链路按字符串透传（api/index.js 的既有纪律），而接口回体里它是数字。
// 两侧都按字符串比，才不允许这一格因为调用方手气不同而消失。
// 注意：本函数只保证「类型不齐」不致命；19 位 id 一旦被调用方 Number() 过，
// 精度在传进来之前就丢了，这里再比也救不回——所以禁 Number() 是调用方的纪律，不是这里的。
it('workspace_id 一侧是字符串、一侧是数字时仍能命中', () => {
  expect(findAppByWorkspace(apps, '102')).toBe(apps[1])
  expect(findAppByWorkspace([{ id: 9, workspace_id: '103' }], 103).id).toBe(9)
})

// 会话还没绑工作区时 currentWorkspaceId 是 null（票 001 / US-03）。而 list_apps 是
// `LEFT JOIN workspace`，行上 workspace_id 可为空——两侧都 String() 之后 null 会自等，
// 把入口挂到一个根本没有工作区的应用上，点进去全是 404。
it('未绑定工作区（null / undefined）时反查不到，且不会命中空工作区的应用行', () => {
  expect(findAppByWorkspace(apps, null)).toBeFalsy()
  expect(findAppByWorkspace(apps, undefined)).toBeFalsy()
  expect(findAppByWorkspace([{ id: 7, workspace_id: null }], null)).toBeFalsy()
})

// 首次进入聊天页时 listApps 还没回来；反查不许在空列表上抛，抛了会把整个输入区带崩。
it('应用列表为空或还没加载时反查不到而不是抛错', () => {
  expect(findAppByWorkspace([], 102)).toBeNull()
  expect(findAppByWorkspace(undefined, 102)).toBeNull()
})

// 主按钮文案是这个页面上唯一的预览状态读数（ADR-0021 后果条：主区只留一个带文字按钮）。
// 落库取值只有这四态（engine.py 的 _set_status 全部调用点）；`stopping` 只是 WS 事件名，
// 库里不存在，不必为它留分支。未识别取值一律按 stopped 出「启动预览」——
// 宁可文案保守，也不给一个点了不知道会怎样的空按钮。
it('预览主按钮文案按 status_dev 变身', () => {
  expect(devActionLabel('stopped')).toBe('启动预览')
  expect(devActionLabel('running')).toBe('重启预览')
  expect(devActionLabel('starting')).toBe('启动中…')
  expect(devActionLabel('error')).toBe('启动预览')
  expect(devActionLabel(undefined)).toBe('启动预览')
  expect(devActionLabel('stopping')).toBe('启动预览')
})

it('预览主按钮只在 starting 期间禁用', () => {
  expect(isDevBusy('starting')).toBe(true)
  expect(isDevBusy('running')).toBe(false)
  expect(isDevBusy('stopped')).toBe(false)
  expect(isDevBusy(undefined)).toBe(false)
})
