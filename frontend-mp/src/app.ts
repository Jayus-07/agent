import { Component, PropsWithChildren } from 'react'
import './app.scss'

/**
 * 应用入口 — Agent 用户端小程序
 * 页面：index（路由分发）/ login / chat（agent 对话）/ placeholder（待做占位）
 */
class App extends Component<PropsWithChildren> {
  render() {
    return this.props.children
  }
}

export default App
