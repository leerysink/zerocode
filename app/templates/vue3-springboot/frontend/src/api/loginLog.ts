import { get, del } from '@/utils/request'
import type { ApiResult, PageResult } from '@/utils/request'

export interface SysLoginLog {
  id: number
  userName: string
  userId: number
  ipaddr: string
  loginLocation: string
  browser: string
  os: string
  status: number
  msg: string
  loginType: number
  loginTime: string
}

export function loginLogPage(params: {
  current?: number
  size?: number
  userName?: string
  status?: number
  loginType?: number
  ipaddr?: string
  beginTime?: string
  endTime?: string
}): Promise<ApiResult<PageResult<SysLoginLog>>> {
  return get<PageResult<SysLoginLog>>('/system/loginLog/page', params)
}

export function loginLogDelete(id: number): Promise<ApiResult> {
  return del(`/system/loginLog/${id}`)
}

export function loginLogClean(): Promise<ApiResult> {
  return del('/system/loginLog/clean')
}
