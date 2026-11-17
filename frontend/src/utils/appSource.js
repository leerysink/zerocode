// 新建应用向导的「仓库地址」载荷组装。判据留在前端一次只为把话说明白，
// 真拦在后端（app/source/ingest.py 的 validate_http_url）。
export const MAX_SOURCE_URL = 1000   // 与后端 guard.MAX_URL_LEN 同值

export function sourcePayload(rawUrl, rawSubpath) {
  const url = String(rawUrl ?? '').trim()
  if (!url) return undefined
  if (url.length > MAX_SOURCE_URL) {
    throw new Error(`仓库地址超过 ${MAX_SOURCE_URL} 字符上限`)
  }
  const subpath = String(rawSubpath ?? '').trim()
  return subpath ? { url, subpath } : { url }
}
