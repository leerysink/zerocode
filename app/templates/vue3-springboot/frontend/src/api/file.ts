import request from '@/utils/request'
import type { ApiResult } from '@/utils/request'

/** 与后端 vo/FileVO 字段一一对应 */
export interface UploadResult {
  fileName: string
  filePath: string
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
