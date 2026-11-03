// 真机预览码的编码层：Reed-Solomon 纠错与掩码交给 qrcode-generator，这里只把模块矩阵折成
// 一条 SVG path。几百个 <rect> 的 DOM 太重，而 v-html 直接塞库产出的整段 SVG 又要多守一道注入面。
import qrcode from 'qrcode-generator'

// ecLevel 'M'（约 15% 容错）：手机屏幕扫码足够，同时让常见地址停在较小的版本上。
export function qrMatrix(text, ecLevel = 'M') {
  if (!text) return null
  let qr
  try {
    qr = qrcode(0, ecLevel)
    qr.addData(text)
    qr.make()
  } catch {
    return null
  }
  const size = qr.getModuleCount()
  let path = ''
  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      if (qr.isDark(row, col)) path += `M${col} ${row}h1v1h-1z`
    }
  }
  return { size, path }
}
