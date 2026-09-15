import { useEffect } from 'react'
import Taro from '@tarojs/taro'
import { getAccessToken } from '@/lib/auth'
import './index.scss'

/**
 * 入口路由分发：有 token 进对话页，无 token 进登录页
 */
export default function Index() {
  useEffect(() => {
    const url = getAccessToken() ? '/pages/chat/index' : '/pages/login/index'
    Taro.reLaunch({ url })
  }, [])

  return null
}
