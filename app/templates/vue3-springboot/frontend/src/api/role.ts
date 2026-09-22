import { get, post, put, del } from '@/utils/request'
import type { ApiResult, PageResult } from '@/utils/request'

export interface SysRole {
  id: number
  roleName: string
  roleKey: string
  dataScope: number
  sort: number
  status: number
  remark: string
  createTime: string
}

export interface SysRoleDTO {
  id?: number
  roleName: string
  roleKey: string
  dataScope?: number
  sort?: number
  status?: number
  remark?: string
  menuIds?: number[]
  deptIds?: number[]
}

/** 角色成员（人员管理弹窗） */
export interface RoleMember {
  userId: number
  username: string
  nickname: string
  phone: string
  deptId?: number
  deptName?: string
  status: number
}

export function rolePage(params: {
  current?: number
  size?: number
  roleName?: string
}): Promise<ApiResult<PageResult<SysRole>>> {
  return get<PageResult<SysRole>>('/system/role/page', params)
}

export function roleList(): Promise<ApiResult<SysRole[]>> {
  return get<SysRole[]>('/system/role/list')
}

export function roleGet(id: number): Promise<ApiResult<SysRole>> {
  return get<SysRole>(`/system/role/${id}`)
}

export function roleMenuIds(id: number): Promise<ApiResult<number[]>> {
  return get<number[]>(`/system/role/${id}/menus`)
}

export function roleDeptIds(id: number): Promise<ApiResult<number[]>> {
  return get<number[]>(`/system/role/${id}/depts`)
}

export function roleAdd(data: SysRoleDTO): Promise<ApiResult> {
  return post('/system/role', data)
}

export function roleUpdate(data: SysRoleDTO): Promise<ApiResult> {
  return put('/system/role', data)
}

export function roleDelete(id: number): Promise<ApiResult> {
  return del(`/system/role/${id}`)
}

/** 角色已授权成员（含停用用户） */
export function roleMembers(id: number): Promise<ApiResult<RoleMember[]>> {
  return get<RoleMember[]>(`/system/role/${id}/members`)
}

/** 角色可授权用户（未授权的启用用户） */
export function roleMemberCandidates(id: number): Promise<ApiResult<RoleMember[]>> {
  return get<RoleMember[]>(`/system/role/${id}/member-candidates`)
}

/** 为角色批量授权用户 */
export function roleMemberBind(id: number, userIds: number[]): Promise<ApiResult> {
  return post(`/system/role/${id}/members`, { userIds })
}

/** 解除用户与该角色的关联 */
export function roleMemberUnbind(id: number, userId: number): Promise<ApiResult> {
  return del(`/system/role/${id}/members/${userId}`)
}
