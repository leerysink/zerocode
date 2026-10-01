// 内置「类桌面」文件管理器的纯逻辑：面包屑、树节点形状、懒加载挂载、大小/时间显示、下载链接。
// 后端 /api/fs/ls 给的 path 是工作区内相对 posix 路径（根为空串），这里全部按该契约对齐。
import { describe, it, expect } from 'vitest'
import { ancestorsOf, attachChildren, buildBreadcrumb, deepestPath, findNode, formatBytes, formatMtime, toTreeNodes, downloadUrl } from './fsNav'

describe('deepestPath', () => {
  it('收起目录后面包屑回到还展开着的最深一层，一个都不剩就是根', () => {
    expect(deepestPath([])).toBe('')
    expect(deepestPath(undefined)).toBe('')
    expect(deepestPath(['src'])).toBe('src')
    expect(deepestPath(['src', 'src/lib', 'a/b'])).toBe('src/lib')
    expect(deepestPath(['a', 'a/b', 'x', 'x/y', 'x/y/z'])).toBe('x/y/z')
  })

  it('只算看得见的那一层：父层收掉了，子层即便还留在展开态里也不算焦点', () => {
    // 收起 src 后树里只剩根层，此时面包屑要跟着退回「工作区」，不能指着看不见的 lib
    expect(deepestPath(['src/lib'])).toBe('')
    expect(deepestPath(['src/lib', 'src/lib/deep'])).toBe('')
    expect(deepestPath(['src', 'src/lib'])).toBe('src/lib')
  })

  it('同一深度的多个展开层给稳定结果（不随插入顺序乱跳）', () => {
    expect(deepestPath(['src', 'docs'])).toBe('src')
    expect(deepestPath(['docs', 'src'])).toBe('docs')
  })
})

describe('toTreeNodes', () => {
  it('/fs/ls 的一层条目变成树节点：目录可展开（children 缺省，展开才拉），文件是叶子', () => {
    expect(toTreeNodes([
      { name: 'src', path: 'src', is_dir: true, size: 0, mtime: 1757900000 },
      { name: 'a.md', path: 'a.md', is_dir: false, size: 12, mtime: 1757900001 },
    ])).toEqual([
      { key: 'src', title: 'src', isLeaf: false, dir: true, size: 0, mtime: 1757900000 },
      { key: 'a.md', title: 'a.md', isLeaf: true, dir: false, size: 12, mtime: 1757900001 },
    ])
  })

  it('空目录给空数组（不是 undefined，渲染层据此区分「没人」与「还没拉」）', () => {
    expect(toTreeNodes([])).toEqual([])
    expect(toTreeNodes(undefined)).toEqual([])
  })
})

describe('attachChildren', () => {
  const roots = () => toTreeNodes([
    { name: 'src', path: 'src', is_dir: true, size: 0, mtime: 1 },
    { name: 'a.md', path: 'a.md', is_dir: false, size: 12, mtime: 2 },
  ])
  const inner = [
    { name: 'inner.txt', path: 'src/inner.txt', is_dir: false, size: 3, mtime: 3 },
    { name: 'lib', path: 'src/lib', is_dir: true, size: 0, mtime: 4 },
  ]

  it('把刚拉到的那一层挂到对应目录节点上，不改变原数组（响应式要拿新引用才会重渲）', () => {
    const origin = roots()
    const next = attachChildren(origin, 'src', inner)

    expect(next[0].children).toEqual(toTreeNodes(inner))
    expect(next[1]).toBe(origin[1]) // 同层无关节点直接复用
    expect(origin[0].children).toBeUndefined() // 原对象没被就地修改
    expect(next).not.toBe(origin)
  })

  it('深层目录也能挂（按 key 逐级往下找）', () => {
    const twoLayer = attachChildren(attachChildren(roots(), 'src', inner), 'src/lib', [
      { name: 'deep.txt', path: 'src/lib/deep.txt', is_dir: false, size: 1, mtime: 5 },
    ])
    expect(twoLayer[0].children[1].children).toEqual(toTreeNodes([
      { name: 'deep.txt', path: 'src/lib/deep.txt', is_dir: false, size: 1, mtime: 5 },
    ]))
  })

  it('目标目录不在树里（已被外部删掉）时原样返回，不造新节点', () => {
    const origin = roots()
    expect(attachChildren(origin, 'gone', inner)).toBe(origin)
  })
})

describe('findNode', () => {
  const tree = () => attachChildren(toTreeNodes([
    { name: 'src', path: 'src', is_dir: true, size: 0, mtime: 1 },
    { name: 'a.md', path: 'a.md', is_dir: false, size: 12, mtime: 2 },
  ]), 'src', [{ name: 'inner.txt', path: 'src/inner.txt', is_dir: false, size: 3, mtime: 3 }])

  it('顶层与深层都能按 key 找回节点（预览区靠它拿大小/时间）', () => {
    expect(findNode(tree(), 'a.md').size).toBe(12)
    expect(findNode(tree(), 'src/inner.txt').title).toBe('inner.txt')
  })

  it('找不到给 null（不抛错），空树也不抛', () => {
    expect(findNode(tree(), 'nope.txt')).toBeNull()
    expect(findNode([], 'a.md')).toBeNull()
    expect(findNode(undefined, 'a.md')).toBeNull()
  })
})

describe('ancestorsOf', () => {
  it('根与一级目录无需展开，深层给出逐级祖先（不含自身）', () => {
    expect(ancestorsOf('')).toEqual([])
    expect(ancestorsOf('src')).toEqual([])
    expect(ancestorsOf('src/lib')).toEqual(['src'])
    expect(ancestorsOf('src/lib/deep')).toEqual(['src', 'src/lib'])
  })
})

describe('buildBreadcrumb', () => {
  it('根用工作区名，逐级带可点击的完整路径', () => {
    expect(buildBreadcrumb('我的应用', '')).toEqual([{ label: '我的应用', path: '' }])
    expect(buildBreadcrumb('我的应用', 'src/components')).toEqual([
      { label: '我的应用', path: '' },
      { label: 'src', path: 'src' },
      { label: 'components', path: 'src/components' },
    ])
  })

  it('没给工作区名时根显示「工作区」', () => {
    expect(buildBreadcrumb('', '')[0].label).toBe('工作区')
  })
})

describe('formatBytes', () => {
  it('大小拿不准（还没拿到节点）给占位符，不谎报成 0 B', () => {
    expect(formatBytes(undefined, false)).toBe('—')
    expect(formatBytes(null, false)).toBe('—')
    expect(formatBytes('不是数字', false)).toBe('—')
  })

  it('负数长度这种脏数据不炸，按 0 B 收口', () => {
    expect(formatBytes(-5, false)).toBe('0 B')
  })

  it('目录不给字节数，其余按 KB/MB/GB 保留一位', () => {
    expect(formatBytes(0, true)).toBe('—')
    expect(formatBytes(0, false)).toBe('0 B')
    expect(formatBytes(512, false)).toBe('512 B')
    expect(formatBytes(1536, false)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 * 1024, false)).toBe('5.0 MB')
    expect(formatBytes(3 * 1024 * 1024 * 1024, false)).toBe('3.0 GB')
  })
})

describe('formatMtime', () => {
  it('epoch 秒渲染成 YYYY-MM-DD HH:mm，缺失给占位符', () => {
    expect(formatMtime(1757900000)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(formatMtime(0)).toBe('—')
    expect(formatMtime(null)).toBe('—')
  })
})

describe('downloadUrl', () => {
  it('带上工作区与路径参数，路径做编码（中文与斜杠不吃亏）', () => {
    expect(downloadUrl(7, 'a.txt')).toBe('/api/fs/download?workspace_id=7&path=a.txt')
    expect(downloadUrl(7, '报告.txt')).toBe('/api/fs/download?workspace_id=7&path=%E6%8A%A5%E5%91%8A.txt')
    expect(downloadUrl(7, 'src/a.txt')).toBe('/api/fs/download?workspace_id=7&path=src%2Fa.txt')
  })
})
