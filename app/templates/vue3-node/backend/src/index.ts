// {{APP_NAME}} 后端骨架：/api 前缀 + health + demo_notes 示例；数据库连接从环境变量读取。
//
// 启动日志：进程一起来就往 **stdout** 打一段 banner（应用、环境、端口、数据库与连通性、就绪耗时），
// `docker compose logs` 与 zerocode 预览日志流采集的都是 stdout，容器起不来时第一眼看这里。
//
// 写法约定（本栈的两条红线，改代码时不要破）：
//   1) SQL 一律走 $1/$2 占位符参数化，不做字符串拼接；
//   2) 通配路由写 Express 5 的 `/{*path}` 形态，写旧版 `'*'` 会在启动期直接抛 PathError。
import express from 'express'
import type { NextFunction, Request, Response } from 'express'
import { db } from './db.js'
import { notesRouter } from './routes/notes.js'

const APP_NAME = "{{APP_NAME}}"
const APP_SLUG = "{{SLUG}}"

/** 连接串脱敏：只留驱动/主机/端口/库名，账号与口令一律不落日志。 */
function maskedDbUrl(url: string): string {
  if (!url) {
    return '未注入 DATABASE_URL'
  }
  try {
    const parsed = new URL(url)
    const port = parsed.port ? `:${parsed.port}` : ''
    return `${parsed.protocol}//${parsed.hostname}${port}${parsed.pathname}`
  } catch {
    return '无法解析（已隐去）'
  }
}

/** 启动期连通性自检：SELECT 1 成功即「可连接」；失败只降级成一行提示，绝不阻断启动。 */
async function dbProbe(): Promise<[boolean, string]> {
  if (!db) {
    return [false, '跳过（DATABASE_URL 为空）']
  }
  try {
    await db.query('SELECT 1')
    return [true, '可连接']
  } catch (e) {
    const first = e instanceof Error ? e.message.split('\n')[0] : String(e)
    return [false, `不可用：${first.slice(0, 160)}`]
  }
}

const app = express()
app.use(express.json())

const api = express.Router()

api.get('/health', async (_req: Request, res: Response) => {
  let ok = false
  if (db) {
    try {
      await db.query('SELECT 1')
      ok = true
    } catch {
      ok = false
    }
  }
  res.json({ status: 'ok', db: ok })
})

api.use('/notes', notesRouter)

app.use('/api', api)

// 未命中的接口：Express 5 的通配写法（旧版 '*' 在启动期就抛错）
app.use('/{*path}', (_req: Request, res: Response) => {
  res.status(404).json({ error: '接口不存在' })
})

// 统一错误出口。Express 5 把 async handler 的 rejection 自动转来这里，
// 所以新增路由不需要自己 try/catch——漏 try/catch 就崩进程是 Express 4 的老坑。
app.use((err: unknown, req: Request, res: Response, _next: NextFunction) => {
  const detail = err as { status?: number; statusCode?: number; message?: string }
  const status = detail.status ?? detail.statusCode ?? 500
  const safe = status >= 400 && status < 600 ? status : 500
  console.error(`[${APP_SLUG}] ${req.method} ${req.originalUrl} 失败:`, err)
  res.status(safe).json({ error: safe < 500 ? (detail.message ?? '请求不合法') : '服务器内部错误' })
})

const started = Date.now()
const port = Number(process.env.APP_PORT ?? process.env.PORT ?? 8000)

app.listen(port, '0.0.0.0', async () => {
  const [dbOk, dbMsg] = await dbProbe()
  const log = (line: string) => console.info(line)
  log('='.repeat(62))
  log(`${APP_NAME}（${APP_SLUG}）后端启动`)
  log(`  运行环境 : ${process.env.APP_ENV ?? 'dev'}`)
  log(`  监听地址 : http://0.0.0.0:${port}`)
  log('  健康检查 : /api/health')
  log(`  数据库   : ${maskedDbUrl(process.env.DATABASE_URL ?? '')}`)
  log(`  连通性   : ${dbMsg}`)
  log(`  就绪耗时 : ${Date.now() - started} ms`)
  log('='.repeat(62))
  if (!dbOk) {
    console.warn(`[${APP_SLUG}] 数据库不可用：接口会报 500，先确认已跑过迁移、连接串指向本应用的库`)
  }
})
