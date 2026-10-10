"""travel/memory/ — 横向 Memory 能力（travel-domain-design-v5.md §7）

偏好、会话摘要和规划恢复数据由本包提供横向能力；读写失败不得阻断主流程。
消费方与具体存储实现以当前调用方和测试为准。本包不是图节点。
"""
