import request from '@/utils/request'
import type { ApiResult } from '@/utils/request'

/** 与后端 vo/FileVO 字段一一对应 */
export interface UploadResult {
  fileName: string
  filePath: string
  /** 绝对路径且已含后端 context 前缀（如 /api/uploads/...），可直接放进 img src，不要再拼 baseURL */
  url: string
  fileSize: number
  fileType: string
  fileHash: string
}

export function uploadFile(file: File): Promise<ApiResult<UploadResult>> {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/file/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
