import { useState } from 'react'
import Taro from '@tarojs/taro'
import { View, Text, Input, Button } from '@tarojs/components'
import { login } from '@/lib/auth'
import './index.scss'

/**
 * 登录页 — 账号密码登录（与 Web 端同一 /api/auth/login 契约）。
 * 微信一键登录（code2session）待后端支持后接入。
 */
export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleLogin = async () => {
    if (!username.trim() || !password) {
      setError('请输入账号和密码')
      return
    }
    setLoading(true)
    setError('')
    try {
      await login(username.trim(), password)
      Taro.reLaunch({ url: '/pages/chat/index' })
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <View className='login-page'>
      <View className='login-card'>
        <Text className='login-title'>Agent 助手</Text>
        <Input
          className='login-input'
          placeholder='账号'
          value={username}
          onInput={(e) => setUsername(e.detail.value)}
        />
        <Input
          className='login-input'
          placeholder='密码'
          password
          value={password}
          onInput={(e) => setPassword(e.detail.value)}
        />
        {error ? <Text className='login-error'>{error}</Text> : null}
        <Button className='login-btn' loading={loading} onClick={handleLogin}>
          登录
        </Button>
      </View>
    </View>
  )
}
