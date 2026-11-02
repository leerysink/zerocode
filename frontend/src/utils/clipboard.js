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
  ta.setAttribute('aria-hidden', 'true')
  // 贴在视口左上角缩成 1px，而不是 top: -9999px 移出文档：跑偏到视口外的节点在 iOS Safari
  // 上根本选不中，execCommand 于是恒返回 false——http 下唯一的降级路也被堵死。
  // opacity 而非 display:none 同理：不渲染的节点同样选不中。
  Object.assign(ta.style, {
    position: 'fixed',
    top: '0',
    left: '0',
    width: '1px',
    height: '1px',
    padding: '0',
    border: '0',
    opacity: '0',
  })
  // 记下用户自己的选区，复制完还回去：这里 select() 会把人家正打算复制的一段文字弄没
  const selection = document.getSelection?.()
  const savedRange = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null
  document.body.appendChild(ta)
  let ok = false
  try {
    // 先 focus 再选：Firefox/Safari 在焦点不在文档上时直接拒绝 execCommand('copy')
    ta.focus({ preventScroll: true })
    // select() 在 iOS Safari 上只选中第一个字符，必须显式给区间
    ta.setSelectionRange(0, value.length)
    ok = document.execCommand('copy') === true
  } catch {
    ok = false // 老浏览器/非浏览器环境里 execCommand 根本不存在
  } finally {
    ta.remove()
    if (savedRange) {
      try {
        selection.removeAllRanges()
        selection.addRange(savedRange)
      } catch { /* 选区已失效（节点被重渲染摘掉）就还不了，不影响复制结果 */ }
    }
  }
  return ok
}
