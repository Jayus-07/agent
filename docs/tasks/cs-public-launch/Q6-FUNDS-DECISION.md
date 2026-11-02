# Q6 首期资金范围核对与决策记录（2026-11-03）

> TASK 要求：核对**全部**资金入口的真实开关状态，不能把一个 disabled 记录视为全资金关闭；首期范围由用户拍板。本文件是核对结果 + 建议，拍板项见 §四。

## 一、资金入口全景（逐项实证）

| # | 入口 | 代码位置 | 开关/门控 | 共享栈运行态（2026-11-03 实测） | 真实资金？ |
|---|---|---|---|---|---|
| 1 | 退款申请（用户确认后写入） | confirmation_flow.py→refund_service.py | `CS_REFUND_REQUEST_MODE`，代码默认 **disabled**；身份闸+幂等账本 | 未设置 → **disabled**（写入关闭） | 否（目标 business-mock） |
| 2 | 退款审核（supervisor 推进） | refund_service.py `_review_mode` / cs_refund_review.py | 双开关：gateway=http **且** refund_mode=http 才启用 | refund_mode=disabled → **disabled** | 否 |
| 3 | 订单取消（用户确认后执行） | confirmation_flow.py `order_cancel` → cancel_order_service → MallService.cancel_order | **无独立模式开关**；身份闸+幂等账本（重复确认只退一次）；gateway=http 时 POST business-service | gateway=**http** → 取消+退款链路**可达**（指向 mock） | 否（退款回 mock 钱包） |
| 4 | 演示商城（充值/下单/支付/取消） | app/api/routes/mall.py（/mall/*） | 9/9 路由有鉴权依赖（逐块核验 unguarded=0） | 已注册 | 否（演示钱包） |
| 5 | 业务网关模式 | `CS_BUSINESS_GATEWAY_MODE`（代码默认 sandbox） | **不是资金开关**；http/sandbox 均承载查询与业务写契约 | **http** | — |

结论：**当前全链路不存在真实资金通道**——所有资金动作终点均为 business-mock（演示/联调事实源，重启清空）。风险性质是「未来接入真实收单时的开关完备性」，不是「现在会动真钱」。

## 二、既有文档偏差（已同步）

- `部署/prod-4c8g/README.md` 曾称 `CS_BUSINESS_GATEWAY_MODE` 默认 disabled（与代码不符：代码默认 sandbox，disabled 不是受支持值）。已按现行代码修正（同 PR）。
- customer-service.md 的渠道/资金语义在 Q1 已落档，本次复核一致。

## 三、首期建议（依据 TASK 预案）

**建议 web 首期不开放真实资金动作**，具体口径：

1. 保持 `CS_REFUND_REQUEST_MODE` 不设置（=disabled）——退款申请写入关闭 ✓（现状已满足）。
2. 保持 `CS_REFUND_REQUEST_MODE`≠http/`CS_BUSINESS_GATEWAY_MODE` 双开组合——退款审核闭环不启用 ✓。
3. `CS_BUSINESS_GATEWAY_MODE=http` 允许保留（查询与订单/物流契约必需，非资金开关）。
4. `/mall/*` 保持演示定位（标注演示、不作为生产收单面）。

## 四、待用户拍板项（Q6 交付物）

| # | 决策点 | 选项 | 后果 |
|---|---|---|---|
| 1 | 首期是否开放真实资金动作 | A. 不开放（建议，现状即满足）；B. 开放退款申请（改 refund_mode）；C. 接真实收单 | B/C 需另立项：真实资金后端、对账、风控 |
| 2 | order_cancel 无独立开关 | A. 接受现状（mock 资金，身份闸+幂等在位）；B. 挂账加 `CS_ORDER_CANCEL_MODE` 类开关（真实后端接入前必须完成） | 选 B 走小 PR；不阻塞受控试用 |

## 五、验收口径

决策记录与实际开关状态一致：§一清单即当前事实；用户拍板后按选项落配置/立项，若选 A（建议）则现状即验收态，无需改配置。
