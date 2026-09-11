# 临时测试脚本：定位并发 LLM 调用慢的原因（单独摘要 vs 单独关键词 vs 并发）
import sys, time, asyncio
sys.path.insert(0, ".")

TEXT = """示例跨境电商公司 跨境电商技术手册 版本：v1.0 生效日期：2026-08-26 文档编号：TECH-001
【仅供测试 内容虚构】 本手册涵盖跨境电商系统的架构、运维与安全规范。
一、系统概述 本系统支撑示例跨境电商公司面向全球买家的商品展示、交易、履约与售后全链路。整体可用性目标 ≥ 99.95%，P99 接口延迟 ≤ 500 ms，错误率 ≤ 0.1%。
二、安全规范 所有生产环境访问必须通过零信任网关认证，密钥轮换周期不超过 90 天。数据分级管理：客户个人信息属 PII 数据，禁止明文存储与跨区传输，日志留存不少于 180 天。
三、运维规范 变更窗口为每周二 02:00-06:00，紧急变更需双人复核并留存审批记录。故障响应 SLA：P1 级 15 分钟内响应，60 分钟内恢复。
四、合规要求 遵守 GDPR 与《个人信息保护法》，跨境数据传输需通过安全评估。供应商准入需通过信息安全审计，合同必须包含数据处理协议条款。
五、访问控制 最小权限原则，按角色分配 RBAC 权限。第三方系统集成必须使用签名凭证，禁止共享账号。异常登录检测触发后自动锁定并通知安全团队。
六、数据备份 每日全量备份+每小时增量备份，异地容灾 RPO ≤ 1 小时，RTO ≤ 4 小时。恢复演练每季度一次。
""" * 5

from backend.rag.preprocessing.keyword import extract_doc_keywords_typed
from backend.rag.preprocessing.metadata import build_llm_summary

async def main():
    sample = TEXT[:4000]
    print(f"文本长度: {len(TEXT)}, sample: {len(sample)}")

    # 1. 单独摘要
    t0 = time.time()
    summ, _ = await build_llm_summary(sample)
    print(f"[单独摘要] {time.time()-t0:.1f}s | {summ[:60]!r}")

    # 2. 单独关键词
    t0 = time.time()
    kw = await asyncio.to_thread(extract_doc_keywords_typed, TEXT, doc_type="security", confidence=0.8, complexity={"token_estimate": 2500})
    print(f"[单独关键词] {time.time()-t0:.1f}s | {[k['word'] for k in kw.llm_keywords][:5]}")

    # 3. 并发（换文本变体绕过缓存）
    TEXT2 = "【修订版 B】" + TEXT + "\n附录：本版本新增供应链风控条款与审计追溯要求。"
    sample2 = TEXT2[:4000]
    async def t_s():
        t = time.time(); r = await build_llm_summary(sample2); return r, time.time()-t
    async def t_k():
        t = time.time(); r = await asyncio.to_thread(extract_doc_keywords_typed, TEXT2, doc_type="security", confidence=0.8, complexity={"token_estimate": 2500}); return r, time.time()-t
    t0 = time.time()
    (s_res, s_dt), (k_res, k_dt) = await asyncio.gather(t_s(), t_k())
    print(f"[并发] 总 {time.time()-t0:.1f}s | 摘要任务 {s_dt:.1f}s, 关键词任务 {k_dt:.1f}s")

asyncio.run(main())
