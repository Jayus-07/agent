数据字典 · 订单域（v2026.07）
==============================

o_order_no    订单号，格式：OR + 14 位时间戳 + 4 位流水号（示例 OR202606151023004312）。
o_status      订单状态，枚举：待支付 / 已支付 / 已发货 / 已完成 / 已退款。
o_amount      订单金额，单位：分，整数，含税。
o_pay_channel 支付渠道，枚举：wechat / alipay / unionpay / corporate。
o_refundable  是否可退，布尔值，虚拟商品默认 false。
