// 统一请求入口：相对 /api + 带 token + 401 回登录页。
//
// 为什么不写绝对域名：H5 与后台管理系统同源（ADR-0039），dev 期经管理端 vite 代理、prod 期经
// 前端 nginx 反代命中同一个后端。写死 http://localhost:8080 之类在容器里立刻失效。
// 只有 APK 壳（不在同源环境里）才需要绝对域名，见平台「移动端」弹框里的打包指引。
export function request(path, { method = 'GET', data } = {}) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: '/api' + path,
      method,
      data,
      header: { Authorization: `Bearer ${uni.getStorageSync('token') || ''}` },
      success: (res) => {
        if (res.statusCode === 401) {
          uni.removeStorageSync('token')
          uni.navigateTo({ url: '/pages/login/index' })
          reject(new Error('登录已过期，请重新登录'))
          return
        }
        if (res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode}`))
          return
        }
        // 后端统一包 Result{code,msg,data}：业务失败也是 HTTP 200，只看状态码会把错误当成功
        const body = res.data
        if (body && typeof body.code === 'number' && body.code !== 200) {
          reject(new Error(body.msg || '请求失败'))
          return
        }
        resolve(body && typeof body.code === 'number' ? body.data : body)
      },
      fail: (err) => reject(new Error(err.errMsg || '网络异常')),
    })
  })
}
