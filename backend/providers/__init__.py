"""backend.providers — 数据 Provider 层

任务书 §3：外部事实（POI / 通勤 / 未来：Booking）统一经 Provider 层进入域逻辑，
Provider 负责搬运事实与打时效标注（observed_at / verification_status /
traffic_aware / is_estimate / fallback_reason），**不做推断、不引入 LLM**。

当前承载旅游域（providers.travel）；后续其他域接入外部数据源时按同构方式扩展。
"""
