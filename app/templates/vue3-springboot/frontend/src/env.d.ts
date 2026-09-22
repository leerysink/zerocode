/// <reference types="vite/client" />

import type { FunctionalComponent, HTMLAttributes, VNodeChild } from 'vue'

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

type IconProps = { spin?: boolean; rotate?: number; twoToneColor?: string } & HTMLAttributes

// 全局注册 Ant Design Vue 图标组件的类型声明
declare module 'vue' {
  export interface GlobalComponents {
    [key: string]: FunctionalComponent<IconProps, {}, VNodeChild> | DefineComponent<any, any, any>
  }
}
