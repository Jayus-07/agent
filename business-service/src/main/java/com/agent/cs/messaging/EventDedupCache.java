package com.agent.cs.messaging;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 事件幂等去重缓存（LRU，进程内）。
 *
 * Kafka at-least-once 投递 + 双写窗口期双方生产，消费方按 event_id
 * 去重。窗口有限（对账/回复场景无需跨重启持久化），超限淘汰最旧。
 * 线程安全：内部 synchronized（消费吞吐远低于缓存争用阈值）。
 */
public class EventDedupCache {

    private final Map<String, Long> seen;

    public EventDedupCache(int maxEntries) {
        this.seen = new LinkedHashMap<>(1024, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, Long> eldest) {
                return size() > maxEntries;
            }
        };
    }

    /**
     * @return true = 首次出现（调用方应处理）；false = 重复事件（应跳过）
     */
    public boolean firstSeen(String eventId) {
        if (eventId == null || eventId.isEmpty()) {
            return true;
        }
        synchronized (seen) {
            return seen.put(eventId, System.currentTimeMillis()) == null;
        }
    }
}
