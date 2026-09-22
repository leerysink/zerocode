import { get, post, put, del } from '@/utils/request'
import type { ApiResult } from '@/utils/request'

// ========== 字典类型 ==========
export interface DictType {
  id: number
  dictName: string
  dictType: string
  status: number
  remark: string
  createTime: string
}

export interface DictTypeDTO {
  id?: number
  dictName: string
  dictType: string
  status?: number
  remark?: string
}

export function dictTypeList(dictName?: string): Promise<ApiResult<DictType[]>> {
  return get<DictType[]>('/dict/type/list', { dictName })
}

export function dictTypeAdd(data: DictTypeDTO): Promise<ApiResult> {
  return post('/dict/type', data)
}

export function dictTypeUpdate(data: DictTypeDTO): Promise<ApiResult> {
  return put('/dict/type', data)
}

export function dictTypeDelete(id: number): Promise<ApiResult> {
  return del(`/dict/type/${id}`)
}

// ========== 字典数据 ==========
export interface DictData {
  id: number
  dictType: string
  dictLabel: string
  dictValue: string
  sort: number
  status: number
  remark: string
  createTime: string
}

export interface DictDataDTO {
  id?: number
  dictType: string
  dictLabel: string
  dictValue: string
  sort?: number
  status?: number
  remark?: string
}

export function dictDataList(dictType: string): Promise<ApiResult<DictData[]>> {
  return get<DictData[]>('/dict/data/list', { dictType })
}

export function dictDataByType(dictType: string): Promise<ApiResult<DictData[]>> {
  return get<DictData[]>(`/dict/data/by-type/${dictType}`)
}

export function dictDataAdd(data: DictDataDTO): Promise<ApiResult> {
  return post('/dict/data', data)
}

export function dictDataUpdate(data: DictDataDTO): Promise<ApiResult> {
  return put('/dict/data', data)
}

export function dictDataDelete(id: number): Promise<ApiResult> {
  return del(`/dict/data/${id}`)
}
