package com.agent.cs.client;

import com.agent.cs.config.AppProperties;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.jsonPath;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

/**
 * AiClient 行为测试：URL 拼接、鉴权/trace 头注入、健康探测降级、响应壳解析
 */
class AiClientTest {

    private AiClient client;
    private MockRestServiceServer server;

    @BeforeEach
    void setUp() {
        AppProperties props = new AppProperties();
        props.getAiService().setBaseUrl("http://ai-service:8000");
        props.getAiService().setInternalToken("secret-token");

        RestClient.Builder builder = RestClient.builder();
        server = MockRestServiceServer.bindTo(builder).build();
        client = new AiClient(props, builder, false);
    }

    @Test
    void healthCheckReturnsTrueOn200() {
        server.expect(requestTo("http://ai-service:8000/health"))
                .andExpect(method(HttpMethod.GET))
                .andExpect(header("X-Internal-Token", "secret-token"))
                .andRespond(withSuccess("ok", MediaType.TEXT_PLAIN));

        assertTrue(client.isHealthy());
        server.verify();
    }

    @Test
    void healthCheckReturnsFalseOnError() {
        server.expect(requestTo("http://ai-service:8000/health"))
                .andRespond(withServerError());

        assertFalse(client.isHealthy());
    }

    @Test
    void listToolsParsesToolManifest() {
        server.expect(requestTo("http://ai-service:8000/internal/ai/tools"))
                .andRespond(withSuccess()
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("""
                                {"count":1,"tools":[{"name":"sql_query",
                                  "description":"执行只读 SQL","server":"sql",
                                  "parameters":{"type":"object"}}]}
                                """));

        List<Map<String, Object>> tools = client.listTools();

        assertEquals(1, tools.size());
        assertEquals("sql_query", tools.get(0).get("name"));
    }

    @Test
    void callToolSendsEnvelopeAndParsesResponse() {
        server.expect(requestTo("http://ai-service:8000/internal/ai/call"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(jsonPath("$.tool").value("rag.search"))
                .andExpect(jsonPath("$.params.q").value("退货政策"))
                .andRespond(withSuccess()
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("""
                                {"ok":true,"tool":"rag.search","server":"rag",
                                 "result":{"answer":"7 天无理由"},"latency_ms":120.5,
                                 "trace_id":"t-1"}
                                """));

        Map<String, Object> resp = client.callTool(
                "rag.search", Map.of("q", "退货政策"));

        assertEquals(Boolean.TRUE, resp.get("ok"));
        assertEquals("rag.search", resp.get("tool"));
    }

    @Test
    void callToolWrapsNetworkFailure() {
        server.expect(requestTo("http://ai-service:8000/internal/ai/call"))
                .andRespond(withServerError());

        org.junit.jupiter.api.Assertions.assertThrows(
                AiClient.AiClientException.class,
                () -> client.callTool("sql_query", Map.of()));
    }
}
