import { get, post, put, del } from '@/utils/request'
import type { ApiResult } from '@/utils/request'

export interface SysDept {
  id: number
  parentId: number
  ancestors: string
  deptName: string
  orderNum: number
  leader: string
  phone: string
  status: number
  createTime: string
}

export interface DeptTreeNode {
  id: number
  parentId: number
  label: string
  children?: DeptTreeNode[]
}

export interface SysDeptDTO {
  id?: number
  parentId: number
  deptName: string
  orderNum?: number
  leader?: string
  phone?: string
  status?: number
}

export function deptList(params?: {
  deptName?: string
  status?: number
}): Promise<ApiResult<SysDept[]>> {
  return get<SysDept[]>('/system/dept/list', params)
}

export function deptTree(): Promise<ApiResult<DeptTreeNode[]>> {
  return get<DeptTreeNode[]>('/system/dept/tree')
}

export function deptGet(id: number): Promise<ApiResult<SysDept>> {
  return get<SysDept>(`/system/dept/${id}`)
}

export function deptAdd(data: SysDeptDTO): Promise<ApiResult> {
  return post('/system/dept', data)
}

export function deptUpdate(data: SysDeptDTO): Promise<ApiResult> {
  return put('/system/dept', data)
}

export function deptDelete(id: number): Promise<ApiResult> {
  return del(`/system/dept/${id}`)
}
