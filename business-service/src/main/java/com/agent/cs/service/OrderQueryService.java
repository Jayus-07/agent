package com.agent.cs.service;

import com.agent.cs.common.ApiException;
import com.agent.cs.config.AppProperties;
import com.agent.cs.guard.PermissionChecker;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 订单查询（只读业务库，替代 Python backend/customer_service/service/order_service.py 的
 * SQL executor 只读访问）。agent_readonly 账号在数据库层强制只读。
 */
@Service
public class OrderQueryService {

    private final JdbcTemplate bizJdbc;
    private final AppProperties props;

    public OrderQueryService(@Qualifier("bizJdbcTemplate") JdbcTemplate bizJdbc, AppProperties props) {
        this.bizJdbc = bizJdbc;
        this.props = props;
    }

    /**
     * 按 order_id 查询订单。
     *
     * @throws ApiException 订单不存在或格式不合法
     */
    public Map<String, Object> getOrderById(String orderId) {
        PermissionChecker.validateOrderId(orderId);
        List<Map<String, Object>> rows = bizJdbc.queryForList(
                "SELECT * FROM \"order\".orders WHERE order_id = ?", orderId);
        if (rows.isEmpty()) {
            throw ApiException.notFound("Order not found: " + orderId);
        }
        return rows.get(0);
    }

    /**
     * 查询用户订单列表（归属校验由 PermissionChecker.checkOrderAccess 在使用处执行）。
     */
    public List<Map<String, Object>> listOrdersByCustomer(String customerId, int limit) {
        return bizJdbc.queryForList(
                "SELECT * FROM \"order\".orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?",
                customerId, limit);
    }

    /**
     * 订单归属校验 + 返回订单（对齐 PermissionChecker.check_order_access）。
     */
    public Map<String, Object> getOrderForUser(String userId, String orderId) {
        Map<String, Object> order = new HashMap<>(getOrderById(orderId));
        PermissionChecker.checkOrderAccess(userId, String.valueOf(order.getOrDefault("customer_id", "")));
        return order;
    }

    /** 业务库连通性探测（供 health 检查扩展） */
    public boolean bizDbReachable() {
        try {
            bizJdbc.queryForObject("SELECT 1", Integer.class);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
