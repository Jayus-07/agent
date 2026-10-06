多域隔离网关级实机（2026-10-06，总会话）
基线：e186a04 全量镜像 9 容器部署；APISIX :9080 网关入口；自签 JWT+会话闸（auth:session jti）
开关操作：DB sys_config TRAVEL_GLOBAL_ENTRY_MODE=guide → 重启 app（refresh_loop 首轮拉取）；验后删行重启回落 env 默认 execute

G1 guide+旅游规划（帮我规划3天2晚的杭州行程）: PASS
  handoff 帧实测 {v:1, target_domain:travel, reason:domain_planning_request,
  params:{destination:杭州, days:3, ...}} + reporter 直出引导话术 80 字符
  （「主对话不直接生成完整行程...带参跳转旅游规划页」）；零 travel_graph_node 节点
G2 一次性查询（福州有什么景点）: PASS 无 handoff、HTTP 200、照常直答
G3 域锁（domain_hint=customer_service）: PASS HTTP 200、无 handoff 卡、进 CS 管线
回滚验证（execute 恢复）: PASS 重新进 travel_graph_node、无 handoff

运行期发现：sys_config 的 get_mode 消费点对已运行进程的 15s 轮询刷新未生效
（设 guide 后等 20s 仍 execute）——重启后首轮拉取生效；refresh_loop 存活性待查，
登记为观测增强项（非阻塞，管理员改开关后本就有 15s 缓存延迟语义）。
