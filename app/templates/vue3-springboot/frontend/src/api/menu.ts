import { get, post, put, del } from '@/utils/request'
import type { ApiResult } from '@/utils/request'

export interface SysMenu {
  id: number
  parentId: number
  menuName: string
  menuType: string
  path: string
  component: string
  permission: string
  icon: string
  sort: number
  visible: number
  status: number
  children?: SysMenu[]
}

export interface TreeNode {
  id: number
  parentId: number
  label: string
  children?: TreeNode[]
}

export function menuList(menuName?: string): Promise<ApiResult<SysMenu[]>> {
  return get<SysMenu[]>('/system/menu/list', { menuName })
}

export function menuTree(): Promise<ApiResult<TreeNode[]>> {
  return get<TreeNode[]>('/system/menu/tree')
}

export function menuAdd(data: Partial<SysMenu>): Promise<ApiResult> {
  return post('/system/menu', data)
}

export function menuUpdate(data: Partial<SysMenu>): Promise<ApiResult> {
  return put('/system/menu', data)
}

export function menuDelete(id: number): Promise<ApiResult> {
  return del(`/system/menu/${id}`)
}
