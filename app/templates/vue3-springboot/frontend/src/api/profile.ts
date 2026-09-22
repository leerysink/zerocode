import { put } from '@/utils/request'
import type { ApiResult } from '@/utils/request'

export interface PasswordDTO {
  oldPassword: string
  newPassword: string
}

export interface ProfileDTO {
  nickname?: string
  phone?: string
  email?: string
}

export function updatePassword(data: PasswordDTO): Promise<ApiResult> {
  return put('/auth/password', data)
}

export function updateProfile(data: ProfileDTO): Promise<ApiResult> {
  return put('/auth/profile', data)
}
