/// <reference types="react" />

// Taro 编译期宏（app.config.ts / 页面 config.ts 使用）
declare const defineAppConfig: (config: unknown) => void
declare const definePageConfig: (config: unknown) => void

declare module '*.png'
declare module '*.gif'
declare module '*.jpg'
declare module '*.jpeg'
declare module '*.svg'
declare module '*.scss'
