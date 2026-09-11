package com.agent.cs.statemachine;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 三个状态机的直译回归测试（对齐 Python backend/tests/customer_service 的关键用例）
 */
class StateMachineTest {

    // ── 会话双维度状态机 ──

    @Test
    void conversationOpenToPendingAllowed() {
        var r = ConversationStateMachine.transition("open", "ai", "pending", null, null);
        assertTrue(r.changed());
        assertEquals("pending", r.statusValue());
        assertEquals("ai", r.modeValue());
    }

    @Test
    void conversationResolvedToPendingForbidden() {
        assertThrows(Exception.class,
                () -> ConversationStateMachine.transition("resolved", "ai", "pending", null, null));
    }

    @Test
    void resolvedMustBeAiMode() {
        // resolved + human → 不变量违反
        assertThrows(Exception.class,
                () -> ConversationStateMachine.transition("open", "ai", "resolved", "human", "agent-1"));
    }

    @Test
    void humanModeRequiresAgent() {
        assertThrows(Exception.class,
                () -> ConversationStateMachine.transition("open", "ai", null, "human", null));
        // 已分配坐席则允许
        assertDoesNotThrow(
                () -> ConversationStateMachine.transition("open", "ai", null, "human", "agent-1"));
    }

    // ── 转人工状态机 ──

    @Test
    void handoffFullPath() {
        var s = HandoffStateMachine.HandoffState.AI_ACTIVE;
        var r1 = HandoffStateMachine.transition(s, HandoffStateMachine.HandoffState.HANDOFF_REQUESTED);
        assertTrue(r1.changed());
        var r2 = HandoffStateMachine.transition(
                HandoffStateMachine.HandoffState.HANDOFF_REQUESTED, HandoffStateMachine.HandoffState.WAITING_HUMAN);
        assertTrue(r2.changed());
        var r3 = HandoffStateMachine.transition(
                HandoffStateMachine.HandoffState.WAITING_HUMAN, HandoffStateMachine.HandoffState.HUMAN_ACTIVE);
        assertTrue(r3.changed());
        var r4 = HandoffStateMachine.transition(
                HandoffStateMachine.HandoffState.HUMAN_ACTIVE, HandoffStateMachine.HandoffState.CLOSED);
        assertTrue(r4.changed());
        assertTrue(r4.state().isTerminal());
    }

    @Test
    void handoffClosedIsTerminal() {
        assertThrows(Exception.class,
                () -> HandoffStateMachine.transition(HandoffStateMachine.HandoffState.CLOSED,
                        HandoffStateMachine.HandoffState.AI_ACTIVE));
    }

    @Test
    void detectExplicitHandoffRequest() {
        var trigger = HandoffStateMachine.detectHandoffTrigger("我要转人工");
        assertNotNull(trigger);
        assertEquals(HandoffStateMachine.TriggerType.EXPLICIT_REQUEST, trigger.triggerType());

        assertNull(HandoffStateMachine.detectHandoffTrigger("我的订单什么时候到"));
        assertNull(HandoffStateMachine.detectHandoffTrigger("   "));
    }

    @Test
    void autoTriggerLowConfidence() {
        var trigger = HandoffStateMachine.evaluateAutoTriggers(2, 0.2, 0.4, 0, 3);
        assertNotNull(trigger);
        assertEquals(HandoffStateMachine.TriggerType.LOW_CONFIDENCE, trigger.triggerType());

        assertNull(HandoffStateMachine.evaluateAutoTriggers(1, 0.2, 0.4, 0, 3));
    }

    // ── 确认状态机 ──

    @Test
    void pendingToExecutingForbidden() {
        // PENDING → EXECUTING 非法（必须经过 CONFIRMED）
        assertThrows(Exception.class,
                () -> ConfirmationStateMachine.transition(
                        ConfirmationStateMachine.ConfirmationState.PENDING_CONFIRMATION,
                        ConfirmationStateMachine.ConfirmationState.EXECUTING));
    }

    @Test
    void fullConfirmationFlow() {
        var s = ConfirmationStateMachine.ConfirmationState.PENDING_CONFIRMATION;
        var r1 = ConfirmationStateMachine.transition(s, ConfirmationStateMachine.ConfirmationState.USER_CONFIRMED);
        assertTrue(r1.changed());
        var r2 = ConfirmationStateMachine.transition(
                ConfirmationStateMachine.ConfirmationState.USER_CONFIRMED,
                ConfirmationStateMachine.ConfirmationState.EXECUTING);
        assertTrue(r2.changed());
        var r3 = ConfirmationStateMachine.transition(
                ConfirmationStateMachine.ConfirmationState.EXECUTING,
                ConfirmationStateMachine.ConfirmationState.SUCCESS);
        assertTrue(r3.state().isTerminal());
    }

    @Test
    void detectConfirmationIntent() {
        assertEquals(ConfirmationStateMachine.ConfirmationIntent.CONFIRM,
                ConfirmationStateMachine.detectConfirmationIntent("好的，确认"));
        assertEquals(ConfirmationStateMachine.ConfirmationIntent.CANCEL,
                ConfirmationStateMachine.detectConfirmationIntent("算了，取消吧"));
        assertEquals(ConfirmationStateMachine.ConfirmationIntent.NONE,
                ConfirmationStateMachine.detectConfirmationIntent("今天天气怎么样"));
    }

    @Test
    void cancelKeywordTakesPrecedence() {
        // Python 语义：先查 CANCEL 关键词，"可以不" 这类句子命中取消
        assertEquals(ConfirmationStateMachine.ConfirmationIntent.CANCEL,
                ConfirmationStateMachine.detectConfirmationIntent("不需要了"));
    }

    @Test
    void interceptStates() {
        assertTrue(HandoffStateMachine.HandoffState.WAITING_HUMAN.shouldIntercept());
        assertFalse(HandoffStateMachine.HandoffState.AI_ACTIVE.shouldIntercept());
    }
}
