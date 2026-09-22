import { get, post } from '@/utils/request'
import type { ApiResult } from '@/utils/request'

export interface LoginDTO {
  username: string
  password: string
}

export interface LoginVO {
  token: string
  userId: number
  username: string
  nickname: string
  /** 未分配部门时后端返 null */
  deptId: number | null
  roles: string[]
  permissions: string[]
}

export function login(data: LoginDTO): Promise<ApiResult<LoginVO>> {
  return post<LoginVO>('/auth/login', data)
}

export function logout(): Promise<ApiResult> {
  return post('/auth/logout')
}

export function getUserInfo(): Promise<ApiResult<LoginVO>> {
  return get<LoginVO>('/auth/info')
}

export function getRouters(): Promise<ApiResult<any[]>> {
  return get<any[]>('/auth/routers')
}
