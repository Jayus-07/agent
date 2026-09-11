package com.agent.cs.client;

import com.agent.cs.config.AppProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Duration;
import java.util.List;
import java.util.Map;

/**
 * AI 服务（Python ai-service）统一调用客户端。
 *
 * Java→Python 同步调用的标准化入口，调用 /internal/ai/* 工具网关：
 *   GET  /internal/ai/tools   工具清单（运行时发现，避免硬编码）
 *   POST /internal/ai/call    调用工具，响应壳 {ok, tool, server, result|error, latency_ms, trace_id}
 *
 * 统一注入 X-Internal-Token（服务间鉴权）与 X-Trace-Id（跨服务 trace 串联，
 * 取 MDC "traceId"，与日志链路一致）。
 */
@Component
public class AiClient {

    public static final String INTERNAL_TOKEN_HEADER = "X-Internal-Token";
    public static final String TRACE_ID_HEADER = "X-Trace-Id";
    public static final String MDC_TRACE_KEY = "traceId";

    private static final Logger log = LoggerFactory.getLogger(AiClient.class);

    private final AppProperties props;
    private final RestClient restClient;

    /** 生产构造：Spring 注入原型 Builder，此处补齐 baseUrl 与超时工厂 */
    @org.springframework.beans.factory.annotation.Autowired
    public AiClient(AppProperties props, RestClient.Builder builder) {
        this(props, builder, true);
    }

    /** 测试构造：applyRequestFactory=false 时不覆盖 Builder 已绑定的 mock 工厂 */
    AiClient(AppProperties props, RestClient.Builder builder, boolean applyRequestFactory) {
        this.props = props;
        AppProperties.AiService cfg = props.getAiService();
        if (applyRequestFactory) {
            SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
            factory.setConnectTimeout(Duration.ofSeconds(3));
            factory.setReadTimeout(Duration.ofMillis(cfg.getTimeoutMs()));
            builder.requestFactory((ClientHttpRequestFactory) factory);
        }
        this.restClient = builder
                .baseUrl(cfg.getBaseUrl())
                .build();
    }

    /** AI 服务是否可达（健康探测，异常一律返回 false 不抛出） */
    public boolean isHealthy() {
        try {
            String body = restClient.get()
                    .uri("/health")
                    .headers(this::injectHeaders)
                    .retrieve()
                    .body(String.class);
            return body != null;
        } catch (Exception e) {
            log.warn("[AiClient] health check failed: {}", e.getMessage());
            return false;
        }
    }

    /** 工具清单（含 name/description/parameters），调用方可据此动态选择工具 */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> listTools() {
        Map<String, Object> resp = restClient.get()
                .uri("/internal/ai/tools")
                .headers(this::injectHeaders)
                .retrieve()
                .body(Map.class);
        if (resp == null || resp.get("tools") == null) {
            throw new AiClientException("listTools: empty response");
        }
        return (List<Map<String, Object>>) resp.get("tools");
    }

    /**
     * 调用 AI 工具。
     *
     * @return 成功时含 result 字段；失败时 ok=false 含 error 字段（工具级失败不抛异常，
     *         由调用方决定降级策略；连接/协议级失败抛 AiClientException）
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> callTool(String tool, Map<String, Object> params) {
        try {
            Map<String, Object> resp = restClient.post()
                    .uri("/internal/ai/call")
                    .headers(this::injectHeaders)
                    .body(Map.of("tool", tool, "params", params == null ? Map.of() : params))
                    .retrieve()
                    .body(Map.class);
            if (resp == null) {
                throw new AiClientException("callTool(" + tool + "): empty response");
            }
            return resp;
        } catch (AiClientException e) {
            throw e;
        } catch (Exception e) {
            throw new AiClientException("callTool(" + tool + ") failed: " + e.getMessage(), e);
        }
    }

    /** 注入服务间鉴权与 trace 透传头 */
    private void injectHeaders(org.springframework.http.HttpHeaders headers) {
        String token = props.getAiService().getInternalToken();
        if (token != null && !token.isBlank()) {
            headers.set(INTERNAL_TOKEN_HEADER, token);
        }
        String traceId = MDC.get(MDC_TRACE_KEY);
        if (traceId != null && !traceId.isBlank()) {
            headers.set(TRACE_ID_HEADER, traceId);
        }
    }

    /** AI 服务调用失败（网络/协议级） */
    public static class AiClientException extends RuntimeException {
        public AiClientException(String message) { super(message); }
        public AiClientException(String message, Throwable cause) { super(message, cause); }
    }
}
