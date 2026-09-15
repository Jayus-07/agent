import { View, Text } from '@tarojs/components'
import './index.scss'

/**
 * 占位页 — 待做功能统一入口（分析任务/知识库/报告等，Phase 5 铺开）
 */
export default function Placeholder() {
  return (
    <View className='placeholder-page'>
      <Text className='placeholder-title'>功能建设中</Text>
      <Text className='placeholder-desc'>该功能小程序端待做，请先使用网页端</Text>
    </View>
  )
}
