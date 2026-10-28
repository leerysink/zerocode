// 附件队列：校验 → 压缩 → 立即上传；会话切换中止在途请求并清空。
import { computed, reactive, ref } from 'vue'
import { uploadAttachment } from '../api'
import {
  compressImage, validateCompressed, validateImageFile,
} from '../utils/imageAttachments'

let seq = 0

export function useImageAttachments(sessionIdGetter) {
  const items = ref([])
  const error = ref('')
  const count = computed(() => items.value.length)
  const uploading = computed(() =>
    items.value.some((i) => i.status === 'compressing' || i.status === 'uploading'))
  const hasFailed = computed(() => items.value.some((i) => i.status === 'failed'))

  function readyItems() {
    return items.value
      .filter((i) => i.status === 'ready')
      .map(({ id, filename, mime, size_bytes, url }) => ({ id, filename, mime, size_bytes, url }))
  }

  function drop(item) {
    if (item.controller) item.controller.abort()
    if (item.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl)
    items.value = items.value.filter((i) => i !== item)
  }

  async function uploadOne(item, sid) {
    item.status = 'compressing'
    const blob = await compressImage(item.file)
    const err = validateCompressed(blob, items.value.filter((i) => i.status === 'ready'))
    if (err) {
      item.status = 'failed'
      item.error = err
      error.value = err
      return
    }
    item.status = 'uploading'
    item.controller = new AbortController()
    const { data } = await uploadAttachment(sid, blob, item.filename, item.controller.signal)
    if (sessionIdGetter() !== sid || !items.value.includes(item)) return
    item.id = data.id
    item.mime = data.mime
    item.size_bytes = data.size_bytes
    item.url = data.url
    item.status = 'ready'
    item.error = ''
  }

  async function addFiles(files) {
    const sid = sessionIdGetter()
    if (!sid) { error.value = '请先选择会话'; return }
    error.value = ''
    for (const file of Array.from(files || [])) {
      const err = validateImageFile(file, items.value.length)
      if (err) { error.value = err; continue }
      // reactive：uploadOne 直接改 item.status，裸对象不会触发依赖它的 computed/template
      const item = reactive({
        key: `a-${++seq}`,
        file,
        filename: file.name || '图片',
        previewUrl: URL.createObjectURL(file),
        status: 'compressing',
        error: '',
        controller: null,
      })
      items.value.push(item)
      try {
        await uploadOne(item, sid)
      } catch (e) {
        if (e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') {
          drop(item)
          continue
        }
        item.status = 'failed'
        item.error = e?.response?.data?.detail || '上传失败'
        error.value = item.error
      }
    }
  }

  async function retry(key) {
    const item = items.value.find((i) => i.key === key)
    if (!item) return
    error.value = ''
    if (item.controller) item.controller = null
    try {
      await uploadOne(item, sessionIdGetter())
    } catch (e) {
      item.status = 'failed'
      item.error = e?.response?.data?.detail || '上传失败'
      error.value = item.error
    }
  }

  function remove(key) {
    const item = items.value.find((i) => i.key === key)
    if (item) drop(item)
  }

  function clear() {
    for (const item of items.value) {
      if (item.controller) item.controller.abort()
      if (item.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl)
    }
    items.value = []
    error.value = ''
  }

  function mergeRejected(list) {
    for (const row of list || []) {
      if (items.value.some((i) => i.id === row.id)) continue
      items.value.push({
        key: `a-${++seq}`,
        file: null,
        filename: row.filename,
        previewUrl: row.url,
        status: 'ready',
        error: '',
        controller: null,
        id: row.id,
        mime: row.mime,
        size_bytes: row.size_bytes,
        url: row.url,
      })
    }
  }

  return {
    items, error, count, uploading, hasFailed,
    addFiles, remove, retry, clear, readyItems, mergeRejected,
  }
}
