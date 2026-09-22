import { get, del } from '@/utils/request'
import type { ApiResult, PageResult } from '@/utils/request'

export interface SysOperLog {
  id: number
  title: string
  businessType: number
  method: string
  requestMethod: string
  operUrl: string
  operIp: string
  operLocation: string
  operParam: string
  jsonResult: string
  status: number
  errorMsg: string
  operUserId: number
  operUserName: string
  costTime: number
  createTime: string
}

export function operLogPage(params: {
  current?: number
  size?: number
  title?: string
  businessType?: number
  status?: number
  operUserName?: string
  beginTime?: string
  endTime?: string
}): Promise<ApiResult<PageResult<SysOperLog>>> {
  return get<PageResult<SysOperLog>>('/system/operLog/page', params)
}

export function operLogDelete(id: number): Promise<ApiResult> {
  return del(`/system/operLog/${id}`)
}

export function operLogClean(): Promise<ApiResult> {
  return del('/system/operLog/clean')
}

// 业务类型映射：必须跟后端 annotation/BusinessType 枚举一致
// （OTHER0/INSERT1/UPDATE2/DELETE3/GRANT4/EXPORT5）；4 不是「导入」，
// SysRoleController 的成员加减就是用 GRANT 打点的，标错会让授权审计显示成导入
export const businessTypeMap: Record<number, string> = {
  0: '其他',
  1: '新增',
  2: '修改',
  3: '删除',
  4: '授权',
  5: '导出',
}

// 业务类型颜色映射
export const businessTypeColorMap: Record<number, string> = {
  0: 'default',
  1: 'success',
  2: 'processing',
  3: 'error',
  4: 'warning',
  5: 'purple',
}
