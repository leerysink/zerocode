import { get, post, put, del } from '@/utils/request'
import type { ApiResult, PageResult } from '@/utils/request'

export interface SysUser {
  id: number
  username: string
  nickname: string
  /** 所属部门ID，未绑定为 null */
  deptId: number | null
  /** 所属部门名称，后端左连接 sys_dept 返回（含已软删部门，保证历史数据照样显示） */
  deptName?: string | null
  phone: string
  email: string
  status: number
  createTime: string
}

export interface SysUserDTO {
  id?: number
  username: string
  password?: string
  nickname?: string
  deptId?: number
  phone?: string
  email?: string
  status?: number
  roleIds?: number[]
}

export function userPage(params: {
  current?: number
  size?: number
  username?: string
  status?: number
}): Promise<ApiResult<PageResult<SysUser>>> {
  return get<PageResult<SysUser>>('/system/user/page', params)
}

export function userGet(id: number): Promise<ApiResult<SysUser>> {
  return get<SysUser>(`/system/user/${id}`)
}

export function userRoleIds(id: number): Promise<ApiResult<number[]>> {
  return get<number[]>(`/system/user/${id}/roles`)
}

export function userAdd(data: SysUserDTO): Promise<ApiResult> {
  return post('/system/user', data)
}

export function userUpdate(data: SysUserDTO): Promise<ApiResult> {
  return put('/system/user', data)
}

export function userDelete(id: number): Promise<ApiResult> {
  return del(`/system/user/${id}`)
}

export function resetPassword(id: number, newPassword: string): Promise<ApiResult> {
  return put(`/system/user/${id}/reset-password`, null, { params: { newPassword } })
}

export function userList(): Promise<ApiResult<SysUser[]>> {
  return get<SysUser[]>('/system/user/list')
}
