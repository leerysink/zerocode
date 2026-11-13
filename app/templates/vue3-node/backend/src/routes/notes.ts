// 示例业务路由：demo_notes 的列表与新增（表结构见 ../schema/schema.sql）。
// 它是给 AI 的写法样板：SQL 一律走 $1/$2 占位符参数化，绝不拼接字符串。
import { Router } from 'express'
import { requireDb } from '../db.js'

interface Note {
  id: string
  title: string
  created_at: Date
}

export const notesRouter = Router()

notesRouter.get('/', async (_req, res) => {
  const rows = await requireDb().any<Note>(
    'SELECT id, title, created_at FROM demo_notes ORDER BY id DESC LIMIT 100')
  res.json(rows)
})

notesRouter.post('/', async (req, res) => {
  const title = String((req.body as { title?: unknown } | undefined)?.title ?? '').trim()
  if (!title) {
    res.status(400).json({ error: 'title 不能为空' })
    return
  }
  const rows = await requireDb().any<Note>(
    'INSERT INTO demo_notes (title) VALUES ($1) RETURNING id, title, created_at', [title])
  res.status(201).json(rows[0])
})
