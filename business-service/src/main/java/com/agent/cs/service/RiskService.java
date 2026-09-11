package com.agent.cs.service;

import com.agent.cs.config.AppProperties;
import org.springframework.stereotype.Service;

/**
 * 风险等级评估（直译自 backend/customer_service/risk.py）
 *
 * 四级风险:
 *   LOW      — 无需确认 (如查询)
 *   MEDIUM   — 需要确认 (如地址修改)
 *   HIGH     — 需要确认 + 审计 (如退款、退货)
 *   CRITICAL — 双重确认 + 人工审核 (如批量退款、删号)
 */
@Service
public class RiskService {

    public enum RiskLevel {
        LOW(0), MEDIUM(1), HIGH(2), CRITICAL(3);

        public final int order;

        RiskLevel(int order) { this.order = order; }

        public static RiskLevel of(String value) {
            for (RiskLevel r : values()) {
                if (r.name().toLowerCase().equals(value)) return r;
            }
            return LOW;
        }

        public String value() { return name().toLowerCase(); }
    }

    private final AppProperties props;

    public RiskService(AppProperties props) {
        this.props = props;
    }

    /**
     * 确定动作的有效风险等级（对齐 assess_action_risk）。
     * 升级规则:
     *  - action_type ∈ criticalActions → CRITICAL
     *  - action_type ∈ highRiskActions → 至少 HIGH
     *  - amount ≥ criticalRefundAmount → 至少 CRITICAL
     */
    public RiskLevel assessActionRisk(String baseRisk, String actionType, Double amount) {
        RiskLevel risk = RiskLevel.of(baseRisk);

        if (actionType != null && props.getRisk().getCriticalActions().contains(actionType)) {
            return RiskLevel.CRITICAL;
        }
        if (actionType != null && props.getRisk().getHighRiskActions().contains(actionType)) {
            if (risk.order < RiskLevel.HIGH.order) {
                risk = RiskLevel.HIGH;
            }
        }
        if (amount != null && amount >= props.getRisk().getCriticalRefundAmount()) {
            if (risk.order < RiskLevel.CRITICAL.order) {
                risk = RiskLevel.CRITICAL;
            }
        }
        return risk;
    }

    public boolean requiresConfirmation(RiskLevel risk) {
        return risk != RiskLevel.LOW;
    }

    public boolean requiresHumanReview(RiskLevel risk) {
        return risk == RiskLevel.CRITICAL;
    }
}
