import { describe, it, expect } from 'vitest'
import {
  fileKind, iconColor, isMarkdown, isReadOnlyPath, languageLabel, languageOf,
} from './fileTypes'

describe('fileKind 扩展名到图标种类', () => {
  it('Vue / JS / TS / JSON / Markdown 各归各kind', () => {
    expect(fileKind('src/App.vue')).toBe('vue')
    expect(fileKind('src/a.js')).toBe('js')
    expect(fileKind('src/a.mjs')).toBe('js')
    expect(fileKind('src/a.cjs')).toBe('js')
    expect(fileKind('src/a.jsx')).toBe('js')
    expect(fileKind('src/a.ts')).toBe('ts')
    expect(fileKind('src/a.tsx')).toBe('ts')
    expect(fileKind('package.json')).toBe('json')
    expect(fileKind('README.md')).toBe('md')
    expect(fileKind('docs/guide.markdown')).toBe('md')
  })

  it('大小写不敏感', () => {
    expect(fileKind('SRC/APP.VUE')).toBe('vue')
    expect(fileKind('README.MD')).toBe('md')
    expect(fileKind('DOCKERFILE')).toBe('docker')
  })

  it('样式 / 后端 / 配置 / 脚本各有kind', () => {
    expect(fileKind('a.css')).toBe('css')
    expect(fileKind('a.scss')).toBe('css')
    expect(fileKind('a.less')).toBe('css')
    expect(fileKind('main.py')).toBe('py')
    expect(fileKind('Main.java')).toBe('java')
    expect(fileKind('init.sql')).toBe('sql')
    expect(fileKind('app.yml')).toBe('yaml')
    expect(fileKind('app.yaml')).toBe('yaml')
    expect(fileKind('run.sh')).toBe('shell')
    expect(fileKind('run.bat')).toBe('bat')
    expect(fileKind('run.cmd')).toBe('bat')
    expect(fileKind('pom.xml')).toBe('xml')
  })

  it('无扩展名与点文件兜底', () => {
    expect(fileKind('Dockerfile')).toBe('docker')
    expect(fileKind('build.dockerfile')).toBe('docker')
    expect(fileKind('.env')).toBe('ini')
    expect(fileKind('.env.local')).toBe('ini')
    expect(fileKind('app.ini')).toBe('ini')
    expect(fileKind('Makefile')).toBe('file')
    expect(fileKind('.gitignore')).toBe('file')
    expect(fileKind('LICENSE')).toBe('file')
  })

  it('图片扩展归图片类（树里可见，读取由后端拒绝）', () => {
    for (const ext of ['png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'bmp']) {
      expect(fileKind(`assets/a.${ext}`)).toBe('image')
    }
    expect(fileKind('logo.svg')).toBe('xml')
  })
})

describe('languageOf Monaco 语言映射', () => {
  it('Vue 走 HTML，未知扩展走 plaintext', () => {
    expect(languageOf('App.vue')).toBe('html')
    expect(languageOf('a.js')).toBe('javascript')
    expect(languageOf('a.ts')).toBe('typescript')
    expect(languageOf('a.json')).toBe('json')
    expect(languageOf('a.md')).toBe('markdown')
    expect(languageOf('a.html')).toBe('html')
    expect(languageOf('a.css')).toBe('css')
    expect(languageOf('a.scss')).toBe('scss')
    expect(languageOf('a.less')).toBe('less')
    expect(languageOf('a.py')).toBe('python')
    expect(languageOf('a.java')).toBe('java')
    expect(languageOf('a.sql')).toBe('sql')
    expect(languageOf('a.yml')).toBe('yaml')
    expect(languageOf('a.sh')).toBe('shell')
    expect(languageOf('a.bat')).toBe('bat')
    expect(languageOf('Dockerfile')).toBe('dockerfile')
    expect(languageOf('a.xml')).toBe('xml')
    expect(languageOf('a.ini')).toBe('ini')
    expect(languageOf('Makefile')).toBe('plaintext')
    expect(languageOf('a.png')).toBe('plaintext')
  })
})

describe('语言显示名', () => {
  it('状态栏用可读名称，未知 id 原样返回', () => {
    expect(languageLabel('html')).toBe('HTML')
    expect(languageLabel('javascript')).toBe('JavaScript')
    expect(languageLabel('markdown')).toBe('Markdown')
    expect(languageLabel('plaintext')).toBe('纯文本')
    expect(languageLabel('weird')).toBe('weird')
  })
})

describe('Markdown 与只读判定', () => {
  it('isMarkdown 只看扩展名', () => {
    expect(isMarkdown('a.md')).toBe(true)
    expect(isMarkdown('a.MARKDOWN')).toBe(true)
    expect(isMarkdown('a.txt')).toBe(false)
  })

  it('只有根 app.json 只读，子目录同名文件不受影响', () => {
    expect(isReadOnlyPath('app.json')).toBe(true)
    expect(isReadOnlyPath('./app.json')).toBe(true)
    expect(isReadOnlyPath('src/app.json')).toBe(false)
    expect(isReadOnlyPath('deploy/app.json')).toBe(false)
  })
})

describe('iconColor 调色板', () => {
  it('每类都有稳定颜色，未知类走中性色', () => {
    expect(iconColor('vue')).toBe('#42b883')
    expect(iconColor('ts')).toBe('#3178c6')
    expect(iconColor('file')).toBe('#8c8c8c')
    expect(iconColor('nope')).toBe('#8c8c8c')
    expect(iconColor('folder')).toBe('#d7ba7d')
  })
})
