# alertmanager.yml.tpl — Alertmanager 配置模板（B4 告警触达，2026-09-21）
#
# compose 的 alertmanager 服务在启动时用 sed 把 __ALERT_WEBHOOK_URL__
# 替换为 .env 的 ALERTMANAGER_WEBHOOK_URL（AM 原生不支持环境变量展开；
# 与应用级告警的 ALERT_WEBHOOK_URL 相互独立）。
#
# receiver 为 generic webhook：POST JSON（Alertmanager 标准告警结构，
# alerts[] 数组），可接自建接收端或任意支持该格式的转发桥。
# 注意：钉钉/企微群机器人要求特定 msgtype 报文，直连会被拒——应用级
# 告警（degradation_alerts）已由 backend/observability/alerts.py 原生
# 支持 wecom/dingtalk/feishu，Prometheus 侧如需直推群机器人需自建
# 转发服务（读 AM webhook → 转格式）。
route:
  receiver: webhook-alert
  group_by: ["alertname", "severity"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: webhook-alert
    webhook_configs:
      - url: "__ALERT_WEBHOOK_URL__"
        # 2026-10-06 企业化：恢复通知必须外发（firing→resolved 双向可达）；
        # 接收端=compose 内 alert-bridge 桥（转企微/飞书/钉钉，见服务注释）
        send_resolved: true
