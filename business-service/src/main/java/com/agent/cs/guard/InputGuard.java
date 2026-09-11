package com.agent.cs.guard;

import java.util.List;
import java.util.regex.Pattern;

/**
 * 客服输入安全检查（直译自 backend/customer_service/security/input_guard.py）
 *
 * 客服专用输入防护层，专注 CS 特有检查：
 *  - 越权意图 (查询他人数据)
 *  - 敏感信息输入 (密码/银行卡)
 *  - 系统探测 (prompt 探测)
 */
public class InputGuard {

    public enum GuardAction { ALLOW, CLARIFY, BLOCK }

    public record CheckResult(GuardAction action, String category, String reason, String message) {
    }

    public record GuardResult(GuardAction action, String category, String reason, String message) {

        public static GuardResult allow() {
            return new GuardResult(GuardAction.ALLOW, null, "", "");
        }

        public boolean blocked() { return action == GuardAction.BLOCK; }
        public boolean needsClarify() { return action == GuardAction.CLARIFY; }
    }

    private static final List<Pattern> INJECTION_PATTERNS = List.of(
            Pattern.compile("忽略(之前|上面|以上)(的|地)?(所有|全部)?(的|地)?(指令|规则|设定)"),
            Pattern.compile("(你现在|请你?|请)(是|作为|扮演)(一个|一名)?"),
            Pattern.compile("(系统|system)\\s*(prompt|提示词|指令|消息)"),
            Pattern.compile("(DAN|do\\s+anything\\s+now)", Pattern.CASE_INSENSITIVE)
    );

    private static final List<Pattern> SQL_INJECTION_PATTERNS = List.of(
            Pattern.compile("'\\s*;\\s*(DROP|DELETE|UPDATE|INSERT)\\s+", Pattern.CASE_INSENSITIVE),
            Pattern.compile("UNION\\s+(ALL\\s+)?SELECT", Pattern.CASE_INSENSITIVE),
            Pattern.compile("\\b(OR|AND)\\s+\\d+\\s*=\\s*\\d+"),
            Pattern.compile("--\\s*$", Pattern.MULTILINE)
    );

    private static final List<Pattern> SCOPE_PATTERNS = List.of(
            Pattern.compile("(查|看|查?看)(一下)?(别人|其他|所有)(用户|人)?(的)?.{0,4}(订单|信息|数据)"),
            Pattern.compile("(帮我|给我)(修改|改|删除|删)(别人|其他)(的)")
    );

    private static final List<Pattern> SENSITIVE_PATTERNS = List.of(
            Pattern.compile("\\b\\d{16,19}\\b"),          // 银行卡号
            Pattern.compile("\\b\\d{17}[\\dXx]\\b"),      // 身份证号
            Pattern.compile("密码(是|为|:)\\s*\\S+")
    );

    private static final List<Pattern> SYSTEM_PROBE_PATTERNS = List.of(
            Pattern.compile("(你的|你是)(什么|哪个)(模型|语言模型|AI)"),
            Pattern.compile("(系统|system)(版本|配置|参数)")
    );

    public GuardResult check(String query) {
        if (query == null || query.isBlank()) {
            return new GuardResult(GuardAction.CLARIFY, "format", "empty_input", "请输入您想咨询的问题。");
        }
        if (query.length() > 2000) {
            return new GuardResult(GuardAction.BLOCK, "format", "too_long", "输入内容过长，请简化后重试。");
        }

        CheckResult injection = firstHit(INJECTION_PATTERNS, query, "injection", "抱歉，无法处理您的请求。", GuardAction.BLOCK);
        if (injection != null) return toGuard(injection);

        CheckResult sql = firstHit(SQL_INJECTION_PATTERNS, query, "sql_injection", "输入格式有误，请检查后重试。", GuardAction.BLOCK);
        if (sql != null) return toGuard(sql);

        CheckResult scope = firstHit(SCOPE_PATTERNS, query, "scope", "您只能查询和操作自己的数据。", GuardAction.BLOCK);
        if (scope != null) return toGuard(scope);

        CheckResult sensitive = firstHit(SENSITIVE_PATTERNS, query, "sensitive",
                "为保护您的安全，请勿在对话中输入银行卡号、密码等敏感信息。", GuardAction.BLOCK);
        if (sensitive != null) return toGuard(sensitive);

        CheckResult probe = firstHit(SYSTEM_PROBE_PATTERNS, query, "system_probe",
                "我是客服助手，可以帮您查询订单、处理售后等问题。请问有什么可以帮您？", GuardAction.CLARIFY);
        if (probe != null) return toGuard(probe);

        return GuardResult.allow();
    }

    private CheckResult firstHit(List<Pattern> patterns, String query, String category,
                                 String message, GuardAction action) {
        for (Pattern p : patterns) {
            if (p.matcher(query).find()) {
                return new CheckResult(action, category, p.pattern(), message);
            }
        }
        return null;
    }

    private GuardResult toGuard(CheckResult r) {
        return new GuardResult(r.action(), r.category(), r.reason(), r.message());
    }
}
