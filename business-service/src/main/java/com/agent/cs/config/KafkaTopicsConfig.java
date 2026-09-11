package com.agent.cs.config;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/**
 * Kafka topic 规划（与 docker-compose 中 KAFKA_CFG_AUTO_CREATE_TOPICS_ENABLE=true 兜底）。
 * 事件契约见 com.agent.cs.messaging.KafkaEventPublisher。
 */
@Configuration
public class KafkaTopicsConfig {

    public static final String TOPIC_CONVERSATION = "cs.conversation.events";
    public static final String TOPIC_MESSAGE = "cs.message.events";
    public static final String TOPIC_HANDOFF = "cs.handoff.events";
    public static final String TOPIC_ACTION = "cs.action.events";
    public static final String TOPIC_AI_REPLY = "ai.reply.events";
    public static final String TOPIC_WHATSAPP_INBOUND = "channel.whatsapp.inbound";

    @Bean
    public NewTopic conversationEventsTopic() {
        return TopicBuilder.name(TOPIC_CONVERSATION).partitions(3).replicas(1).build();
    }

    @Bean
    public NewTopic messageEventsTopic() {
        return TopicBuilder.name(TOPIC_MESSAGE).partitions(3).replicas(1).build();
    }

    @Bean
    public NewTopic handoffEventsTopic() {
        return TopicBuilder.name(TOPIC_HANDOFF).partitions(3).replicas(1).build();
    }

    @Bean
    public NewTopic actionEventsTopic() {
        return TopicBuilder.name(TOPIC_ACTION).partitions(3).replicas(1).build();
    }

    @Bean
    public NewTopic aiReplyEventsTopic() {
        return TopicBuilder.name(TOPIC_AI_REPLY).partitions(3).replicas(1).build();
    }

    @Bean
    public NewTopic whatsappInboundTopic() {
        return TopicBuilder.name(TOPIC_WHATSAPP_INBOUND).partitions(3).replicas(1).build();
    }
}
