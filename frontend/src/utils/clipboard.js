// 复制到剪贴板：两条路都试一遍，只回「成/不成」，报错与兜底展示归调用方。
// 为什么要两条：navigator.clipboard（async Clipboard API）只在安全上下文存在，用 http + 服务器
// IP 直连控制台时它是 undefined；execCommand('copy') 虽然早已废弃，但在两种上下文里都还能用，
// 是 http 下唯一还能写剪贴板的办法。反过来 Clipboard API 也可能当场被拒（窗口失焦、站点权限没给），
// 所以两条是「依次降级」而不是「二选一」。

export async function copyText(text) {
  const value = String(text ?? '')
  if (!value) return false
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch { /* 被拒 → 继续往下降级 */ }
  }
  return legacyCopy(value)
}

function legacyCopy(value) {
  const ta = document.createElement('textarea')
  ta.value = value
  // readonly：避免聚焦时移动端键盘弹起，也让部分浏览器允许整段选中
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.top = '-9999px'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  let ok = false
  try {
    ok = document.execCommand('copy') === true
  } catch {
    ok = false // 老浏览器/非浏览器环境里 execCommand 根本不存在
  } finally {
    ta.remove()
  }
  return ok
}
