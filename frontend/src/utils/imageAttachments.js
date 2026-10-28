// 聊天图片附件：类型/体积校验、浏览器端压缩与客户端消息 ID。
// 纯决策函数与 canvas 压缩分开，happy-dom 只测前者。
export const MAX_ATTACHMENTS = 4
export const MAX_SOURCE_BYTES = 20 * 1024 * 1024
export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
export const MAX_MESSAGE_BYTES = 10 * 1024 * 1024
export const MAX_IMAGE_EDGE = 2048
export const TARGET_IMAGE_BYTES = 1.5 * 1024 * 1024
export const ACCEPTED_IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

export function validateImageFile(file, currentCount) {
  if (currentCount + 1 > MAX_ATTACHMENTS) return `最多 ${MAX_ATTACHMENTS} 张图片`
  if (!ACCEPTED_IMAGE_MIMES.includes(file.type)) {
    return '不支持的文件类型，仅支持 PNG / JPEG / WebP / GIF'
  }
  if (file.size > MAX_SOURCE_BYTES) return '单张图片不能超过 20MB'
  return null
}

export function validateCompressed(blob, items) {
  if (blob.size > MAX_ATTACHMENT_BYTES) return '压缩后图片仍超过 5MB，请换一张'
  const total = items.reduce((n, it) => n + (it.size_bytes || 0), 0) + blob.size
  if (total > MAX_MESSAGE_BYTES) return '单条消息图片合计不能超过 10MB'
  return null
}

export function formatBytes(n) {
  const v = Number(n) || 0
  if (v < 1024) return `${v} B`
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`
  return `${(v / (1024 * 1024)).toFixed(1)} MB`
}

export function newClientMsgId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

async function decodeImage(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file)
    } catch {
      // Safari 旧版/动图解码失败：回退到 Image + object URL
    }
  }
  return await new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => { URL.revokeObjectURL(url); resolve(img) }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('图片解码失败')) }
    img.src = url
  })
}

function toBlob(canvas, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('图片压缩失败'))),
      'image/jpeg', quality)
  })
}

export async function compressImage(file) {
  const src = await decodeImage(file)
  const w = src.width || src.naturalWidth
  const h = src.height || src.naturalHeight
  if (!w || !h) throw new Error('图片解码失败')
  const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(w, h))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(w * scale))
  canvas.height = Math.max(1, Math.round(h * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('浏览器不支持图片压缩')
  // JPEG 无透明通道：先铺白，避免透明区域变黑
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(src, 0, 0, canvas.width, canvas.height)
  if (src.close) src.close()
  let blob = null
  for (const quality of [0.85, 0.7, 0.5]) {
    blob = await toBlob(canvas, quality)
    if (blob.size <= TARGET_IMAGE_BYTES) break
  }
  return blob
}
