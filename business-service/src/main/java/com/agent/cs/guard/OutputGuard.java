package com.agent.cs.guard;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 客服输出防护（直译自 backend/customer_service/security/output_guard.py）
 *
 * 五层过滤:
 *  1. 去除内部信息 (SQL / 路径 / 堆栈 / 密钥 / HTML 注释)
 *  2. 去除未授权承诺 (赔偿 / 保证)
 *  3. 其他用户信息脱敏
 *  4. 转接内部信息过滤
 *  5. 投诉内部信息过滤
 */
public class OutputGuard {

    public record OutputGuardResult(String text, boolean filtered, List<String> reasons) {
    }

    private static final List<Pattern> INTERNAL_PATTERNS = List.of(
            Pattern.compile("SELECT\\s+.+?\\bFROM\\b", Pattern.CASE_INSENSITIVE | Pattern.DOTALL),
            Pattern.compile("(?:INSERT|UPDATE|DELETE|DROP|ALTER)\\s+", Pattern.CASE_INSENSITIVE),
            Pattern.compile("/[\\w/.\\-]+\\.py\\b"),
            Pattern.compile("Traceback\\s*\\(most recent call last\\)"),
            Pattern.compile("(?:API_KEY|SECRET|PASSWORD)\\s*=\\s*\\S+", Pattern.CASE_INSENSITIVE),
            Pattern.compile("<!--.*?-->", Pattern.DOTALL),
            Pattern.compile("File \"[^\"]+\\.py\", line \\d+")
    );

    private static final List<Pattern> PROMISE_PATTERNS = List.of(
            Pattern.compile("我们(一定|保证|承诺)(会|将)"),
            Pattern.compile("(赔偿|补偿).*?\\d+"),
            Pattern.compile("(退款|赔付).*?\\d+\\s*(元|块|美元)")
    );

    private static final List<Pattern> HANDOFF_INTERNAL_PATTERNS = List.of(
            Pattern.compile("ai_active|handoff_requested|waiting_human|human_active"),
            Pattern.compile("HANDOFF-[A-Z0-9]{8}"),
            Pattern.compile("HandoffState\\.\\w+"),
            Pattern.compile("handoff_state[\"']?\\s*[:=]"),
            Pattern.compile("trigger_type[\"']?\\s*[:=]")
    );

    private static final List<Pattern> COMPLAINT_INTERNAL_PATTERNS = List.of(
            Pattern.compile("COMPLAINT-[A-Z0-9]{8}"),
            Pattern.compile("severity[\"']?\\s*[:=]\\s*(low|medium|high)"),
            Pattern.compile("matched_patterns[\"']?\\s*[:=]"),
            Pattern.compile("投诉工单.*?内部")
    );

    public OutputGuardResult check(String response, Map<String, Object> csContext) {
        if (response == null || response.isEmpty()) {
            return new OutputGuardResult(response, false, List.of());
        }

        List<String> reasons = new ArrayList<>();
        String text = response;

        for (Pattern p : INTERNAL_PATTERNS) {
            if (p.matcher(text).find()) {
                reasons.add("internal_info");
                text = p.matcher(text).replaceAll("[已过滤]");
            }
        }
        for (Pattern p : PROMISE_PATTERNS) {
            if (p.matcher(text).find()) {
                reasons.add("unauthorized_promise");
                text = p.matcher(text).replaceAll("[已过滤]");
            }
        }
        if (csContext != null) {
            String currentUserId = String.valueOf(csContext.getOrDefault("authenticated_user_id", ""));
            if (!currentUserId.isEmpty()) {
                Object known = csContext.get("known_other_user_ids");
                if (known instanceof List<?> ids) {
                    for (Object otherId : ids) {
                        String oid = String.valueOf(otherId);
                        if (!oid.equals(currentUserId) && text.contains(oid)) {
                            text = text.replace(oid, "[其他用户信息]");
                            reasons.add("other_user_info");
                        }
                    }
                }
            }
        }
        for (Pattern p : HANDOFF_INTERNAL_PATTERNS) {
            if (p.matcher(text).find()) {
                reasons.add("handoff_internal");
                text = p.matcher(text).replaceAll("[已过滤]");
            }
        }
        for (Pattern p : COMPLAINT_INTERNAL_PATTERNS) {
            if (p.matcher(text).find()) {
                reasons.add("complaint_internal");
                text = p.matcher(text).replaceAll("[已过滤]");
            }
        }

        return new OutputGuardResult(text, !reasons.isEmpty(), reasons);
    }
}
