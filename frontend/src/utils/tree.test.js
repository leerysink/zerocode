import { describe, it, expect } from 'vitest'
import { flattenTree } from './tree'

it('展平文件节点（跳过目录本身，深入子级）', () => {
  const tree = [
    { name: 'src', type: 'dir', path: 'src', children: [
      { name: 'a.js', type: 'file', path: 'src/a.js' },
      { name: 'sub', type: 'dir', path: 'src/sub', children: [
        { name: 'b.ts', type: 'file', path: 'src/sub/b.ts' }] }] },
    { name: 'README.md', type: 'file', path: 'README.md' }]
  expect(flattenTree(tree)).toEqual([
    { path: 'src/a.js', name: 'a.js' },
    { path: 'src/sub/b.ts', name: 'b.ts' },
    { path: 'README.md', name: 'README.md' }])
})
