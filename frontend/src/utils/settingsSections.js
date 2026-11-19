// 设置中心的分区与配置行清单（009）：从 SettingsView 抽出来，让「34 个键是否都在表里、
// 有没有重复绑定、min/max 与后端区间是否对齐」能被测试直接核，而不是靠扫模板源码。
// 每行的 min/max/step 必须是后端夹取区间的副本：抄错等于把越界值发给后端再被悄悄回落。

export const openFolderModeOptions = [
  { value: 'web', label: '内置文件管理器（默认，推荐）' },
  { value: 'auto', label: '自动判定（本机访问直开）' },
  { value: 'system', label: '强制系统资源管理器' },
]

// 分区与配置行：type 决定控件，min/max/step 与后端夹取区间一致。
// 带 groups 的分区共用同一条吸底保存条（都写 config store）；模型库/外观/账号没有 groups。
export const SETTINGS_SECTIONS = [
  { key: 'models', label: '模型库', desc: '多个模型档案并存：对话模型与向量模型分两类，默认标记各自生效。' },
  {
    key: 'access', label: '模型接入', desc: '未指定默认对话模型时的回落档案，也是测试连接的入口。',
    groups: [{
      title: '模型接入',
      rows: [
        { form: 'baseUrl', type: 'text', label: 'Base URL', placeholder: 'https://api.deepseek.com/v1' },
        { form: 'model', type: 'text', label: '模型', placeholder: 'deepseek-chat' },
        { form: 'apiKey', type: 'password', label: 'API Key', placeholder: 'sk-***',
          desc: '保存后以掩码 sk-*** 显示；重新输入即覆盖' },
        { form: 'supportsVision', type: 'switch', label: '支持图片输入',
          desc: '仅在未配置默认对话模型时生效；有默认模型时以模型库设置为准' },
      ],
    }],
  },
  {
    key: 'behavior', label: 'Agent 行为', desc: 'AI 动手之前问不问、问几轮、怎么委派子任务。',
    groups: [
      {
        title: '确认策略',
        rows: [
          { form: 'writeConfirm', type: 'switch', label: '写入前确认',
            desc: 'AI 落盘前先给你看改动再动手，覆盖 write/edit/delete/move/revert 五个工具。数据库写入不可回滚，恒确认，不受这一项影响。' },
          { form: 'previewConfirmRunning', type: 'switch', label: '重启预览需确认',
            desc: '只在预览正在运行时拦一道：重启会中断你正在用的调试。预览停着时模型直接启动、不问；关掉则运行中的也直接重启。这两项都是平台级配置，保存后对所有账号生效。' },
        ],
      },
      {
        title: '需求澄清',
        rows: [
          { form: 'clarifyEnabled', type: 'switch', label: '需求澄清模式',
            desc: '开启后 AI 改文件前会先跟你对齐需求（先给解读、再逐题确认）；这一项按账户生效，关掉只影响你自己' },
          { form: 'askRemindAt', type: 'number', label: '提问提醒题数', min: 0,
            desc: '本轮问满 N 题时提醒模型停止追问（软提醒，不禁止提问）；0=不提醒' },
          { form: 'askTimeoutSeconds', type: 'number', label: '提问等待超时（秒）', min: 0,
            desc: '0=不限制' },
        ],
      },
      {
        title: '执行回合与委派',
        rows: [
          { form: 'maxToolRounds', type: 'number', label: '最大工具轮数', min: 0, desc: '0=不限制' },
          { form: 'historyWindow', type: 'number', label: '历史窗口（条数）', min: 0 },
          { form: 'subrunMaxTasks', type: 'number', label: '单批委派子任务上限（个）', min: 1, max: 8,
            desc: '账户级、只影响自己；一次 delegate 最多并行几个只读子 agent，1..8' },
          { form: 'subrunMaxRounds', type: 'number', label: '子任务轮数上限（轮）', min: 0, max: 50,
            desc: '账户级、只影响自己；每个子 agent 最多跑几轮，0 表示不限制（有限值最大 50）' },
        ],
      },
    ],
  },
  {
    key: 'context', label: '上下文与记忆', desc: '聊多长开始摘要、每次带几条记忆回来。',
    groups: [
      {
        title: '上下文压缩',
        rows: [
          { form: 'autoCompactChars', type: 'number', label: '自动压缩阈值（字符）', min: 0, step: 1000,
            desc: '水位之后的消息估算超过这个字符数就自动摘要，0=关闭自动压缩' },
          { form: 'compactMinNew', type: 'number', label: '压缩最少新增消息数', min: 0,
            desc: '距上次压缩至少新增这么多条才允许再压，0=不设下限' },
          { form: 'compactKeepRecent', type: 'number', label: '压缩保留原文条数', min: 1,
            desc: '最近 N 条消息不进摘要，保持原文' },
        ],
      },
      {
        title: '记忆召回',
        rows: [
          { form: 'memoryEnabled', type: 'switch', label: '注入召回记忆',
            desc: '关掉只是不再往提示词里塞记忆，AI 仍可写入记忆' },
          { form: 'memoryTopK', type: 'number', label: '召回条数', min: 1, max: 100,
            desc: '每次最多带几条记忆，1..100' },
          { form: 'memoryMinSimilarity', type: 'number', label: '相似度阈值', min: -1, max: 1, step: 0.05,
            desc: '-1..1；-1 表示一条都不因阈值被滤掉' },
          { form: 'memoryMaxTokens', type: 'number', label: '记忆注入预算（token）', min: 1, max: 20000, step: 100,
            desc: '1..20000；召回按分数排序逐条累计，触顶的整条丢弃' },
        ],
      },
    ],
  },
  {
    key: 'tools', label: '联网与工具', desc: '浏览器走查、公网读取、应用库查询这三条通道的量闸。',
    groups: [
      {
        title: '浏览器检查',
        rows: [
          { form: 'browserMaxPerRun', type: 'number', label: '只读检查次数上限（次/任务）', min: 0, max: 100,
            desc: '0=不限，最大 100' },
          { form: 'browserMaxStepsPerRun', type: 'number', label: '交互步数上限（步/任务）', min: 0, max: 200,
            desc: '0=不限，最大 200' },
          { form: 'browserTimeoutSeconds', type: 'number', label: '单步超时（秒）', min: 10, max: 120,
            desc: '打开页面与检查的 HTTP 超时，10..120' },
          { form: 'browserExpectTimeoutMs', type: 'number', label: '断言等待超时（毫秒）', min: 500, max: 8000, step: 100,
            desc: 'expect 轮询上限，500..8000' },
          { form: 'browserSettleMs', type: 'number', label: '页面静置等待（毫秒）', min: 0, max: 5000, step: 100,
            desc: '导航后留给动画的时间，0..5000' },
          { form: 'browserInteractiveMax', type: 'number', label: '交互元素清单条数', min: 1, max: 200,
            desc: '喂给模型的可点元素清单长度，1..200，调大即调高每轮 token' },
        ],
      },
      {
        title: '联网工具',
        rows: [
          { form: 'webMaxFetchesPerRun', type: 'number', label: '网页读取次数上限（次/任务）', min: 0, max: 200,
            desc: 'web_fetch 每次任务最多抓几个页面，0=不限，最大 200；公网走查的步数沿用上面的交互步数上限' },
        ],
      },
      {
        title: '应用数据库工具',
        rows: [
          { form: 'dbRowsLimit', type: 'number', label: '查询结果行数上限', min: 10, max: 1000,
            desc: '返回给模型的行数，10..1000' },
          { form: 'dbRowsAssertMax', type: 'number', label: '断言比对行数上限', min: 1, max: 1000,
            desc: 'rows 断言两侧各比对的行数，1..1000' },
        ],
      },
    ],
  },
  {
    key: 'runtime', label: '环境与预览', desc: '预览进程的等待与日志预算，以及数据库环境读数。',
    groups: [
      {
        title: '执行与预览',
        rows: [
          { form: 'steeringQueueMax', type: 'number', label: '插话队列上限（条）', min: 0,
            desc: '执行中最多排几条插话，0=禁用插话' },
          { form: 'previewWaitSeconds', type: 'number', label: '预览启动等待（秒）', min: 0, max: 900,
            desc: 'preview_restart 单次最长等待，0..900' },
          { form: 'previewLogLines', type: 'number', label: '预览日志行数', min: 10, max: 300,
            desc: '返回给模型的最近日志行数，10..300' },
          { form: 'previewMaxRestarts', type: 'number', label: '预览重启次数上限', min: 0,
            desc: '单次任务内启动/重启预览的次数上限；0=不限制' },
        ],
      },
      {
        title: '工作区打开方式',
        rows: [
          { form: 'openFolderMode', type: 'select', label: '打开文件夹方式', options: openFolderModeOptions,
            desc: '默认在页面里弹出内置文件管理器（网页版目录）；自动判定=本机访问直开系统资源管理器、远端访问走网页；强制直开留给确定同机的场合' },
        ],
      },
    ],
  },
  { key: 'appearance', label: '外观', desc: '控制台深浅色是全局偏好，与右上角账户栏的快捷开关同源。' },
  { key: 'account', label: '账号', desc: '当前登录身份。应用与配置的所有权都挂在这个账号上。' },
]
