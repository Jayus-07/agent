"""business-service 的状态化 Mock；契约详见同目录 contract.md。

订单按用户稳定生成，售后申请在 Mock 进程内保留并支持幂等创建、原因更新
和状态查询。Mock 不连外部依赖，也不执行资金退款。
"""
