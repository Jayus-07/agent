package com.agent.cs;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * 客服业务系统入口（Java / Spring Boot）
 *
 * 职责边界（对齐目标架构）：
 *  - 工单/客户/会话/审计/确认流/转人工 的持久化与状态机
 *  - WhatsApp 渠道接入（webhook 收消息 + Graph API 发消息）
 *  - Kafka 事件发布（cs.conversation.events 等）
 *
 * AI 职责（RAG/Agent/LLM 编排）留在 Python ai-service，通过 Kafka + 内部 REST 协作。
 */
@SpringBootApplication
@ConfigurationPropertiesScan
public class CsApplication {

    public static void main(String[] args) {
        SpringApplication.run(CsApplication.class, args);
    }
}
