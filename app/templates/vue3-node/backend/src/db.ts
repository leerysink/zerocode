// 数据库连接的唯一入口：连接串只从环境变量读，代码与仓库里都不得出现口令。
import pgPromise from 'pg-promise'
import type { IDatabase } from 'pg-promise'

const pgp = pgPromise()

// 本栈只认 postgresql:// / postgres:// 形态。平台给 vue3-node 注入的就是纯连接串——
// 它与 vue3-fastapi 那侧的 SQLAlchemy `postgresql+asyncpg://` 方言前缀不是同一种形态，
// 把带方言的串拿过来会连不上库（表现为预览起来了、后端一直连不上，而不是创建报错）。
const connectionString = process.env.DATABASE_URL ?? ''

export const db: IDatabase<object> | null = connectionString ? pgp(connectionString) : null

/** 取连接句柄；未配置数据库时抛错，由 Express 5 的错误中间件统一转成 500。 */
export function requireDb(): IDatabase<object> {
  if (!db) {
    throw new Error('DATABASE_URL 未配置')
  }
  return db
}
