/**
 * 缺陷8（2026-09-23）回归：认领成功后工作台状态必须立即更新。
 *
 * 背景：handleClaim 此前用 claim 前的旧队列快照 selectConversation(旧 item)，
 * composer 解锁依赖 selected.handoff_state === "human_active"。WS 在线时
 * conversation.claimed 事件随后到达把状态掰回来，问题被掩盖；polling 降级
 * （WS 已断）时事件不可达 → API 认领成功但输入框永久 disabled。
 *
 * 本测试全程把 useAgentSocket 钉死在 connected=false（无任何 WS 事件路径），
 * 只允许 claimConversation 的 REST 返回驱动 UI —— 锁定「API 返回是发起方
 * 状态更新的第一依据，WS 只是最终一致性通道」这一契约。
 *
 * 无 @testing-library：react-dom/client + act 直接渲染（vitest happy-dom）。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react-dom/test-utils";

// vi.hoisted：mock 工厂在 import 提升阶段执行
const csMock = vi.hoisted(() => ({
  acceptOffer: vi.fn(),
  claimConversation: vi.fn(),
  closeConversation: vi.fn(),
  declineOffer: vi.fn(),
  getHandoffMessages: vi.fn(),
  getHandoffQueue: vi.fn(),
  getMyOffers: vi.fn(),
  notifyAgentTyping: vi.fn(),
  sendAgentMessage: vi.fn(),
}));
vi.mock("@/api/cs", () => csMock);

const wsMock = vi.hoisted(() => ({
  // 默认 WS 恒断：缺陷8 场景就是 polling 降级
  useAgentSocket: vi.fn(() => ({ connected: false })),
}));
vi.mock("@/lib/csAgentWs", () => wsMock);

vi.mock("@/components/agent/AgentAssistPanel", () => ({ default: () => null }));

import HandoffWorkbenchPage from "./page";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const queueItem = {
  conversation_id: "conv-defect-8",
  user_id: "user-1",
  handoff_state: "waiting_human",
  trigger_type: "explicit",
  trigger_reason: "用户要求转人工",
  updated_at: "2026-09-23T00:00:00Z",
  last_message_preview: "我要退货",
};

async function flushAct(ms = 25) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

async function waitFor(predicate: () => boolean, label: string, timeout = 3000) {
  const start = Date.now();
  while (!predicate()) {
    if (Date.now() - start > timeout) {
      throw new Error(`waitFor 超时: ${label}`);
    }
    await flushAct();
  }
}

describe("handoff 工作台认领（缺陷8）", () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    document.body.innerHTML = "";
    container = document.createElement("div");
    document.body.appendChild(container);

    wsMock.useAgentSocket.mockReturnValue({ connected: false });
    csMock.getHandoffQueue.mockResolvedValue({
      items: [queueItem],
      total: 1,
    });
    csMock.getMyOffers.mockResolvedValue({ items: [] });
    csMock.getHandoffMessages.mockResolvedValue({ messages: [], last_id: 0 });
  });

  const mount = async () => {
    await act(async () => {
      root = createRoot(container);
      root!.render(createElement(HandoffWorkbenchPage));
    });
    await flushAct();
  };

  it("WS 断开（polling 降级）下认领成功 → 立即 human_active 并解锁 composer，不等 WS", async () => {
    await mount();

    // 队列轮询（降级通道）已拉到待接入工单
    await waitFor(
      () => !!container.textContent?.includes("认领"),
      "队列出现认领按钮",
    );
    // 认领前：详情未选中，composer 不存在
    expect(container.textContent).toContain("排队中");

    csMock.claimConversation.mockResolvedValue({
      conversation_id: queueItem.conversation_id,
      handoff_state: "human_active",
      agent_id: "agent-1",
      already_claimed: false,
    });

    const claimButton = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "认领",
    );
    expect(claimButton).toBeTruthy();
    await act(async () => {
      claimButton!.click();
    });

    // 修复点：REST 返回驱动状态更新，无任何 WS 事件参与
    await waitFor(
      () => !!container.textContent?.includes("人工处理中"),
      "选中会话徽章翻转为人工处理中",
    );

    // 队列项不再是 waiting_human → 认领按钮消失
    expect(
      Array.from(container.querySelectorAll("button")).some(
        (b) => b.textContent?.trim() === "认领",
      ),
    ).toBe(false);

    // composer 解锁：输入框 placeholder 翻转且不再 disabled（直接由
    // selected.handoff_state 驱动）；发送按钮是图标按钮，与输入框同容器
    const replyInput = container.querySelector<HTMLInputElement>(
      'input[placeholder="输入回复内容，回车发送"]',
    );
    expect(replyInput).toBeTruthy();
    expect(replyInput!.disabled).toBe(false);
    const sendButton = replyInput!.parentElement?.querySelector("button");
    expect(sendButton).toBeTruthy();
    expect(sendButton!.disabled).toBe(false);

    expect(csMock.claimConversation).toHaveBeenCalledTimes(1);
    expect(csMock.claimConversation).toHaveBeenCalledWith("conv-defect-8");
  });

  it("后端返回仍是旧状态的防御断言：返回值是唯一事实源（显示后端状态而非本地猜测）", async () => {
    await mount();
    await waitFor(
      () => !!container.textContent?.includes("认领"),
      "队列出现认领按钮",
    );

    // 即使后端返回非常规状态，UI 也应忠实显示（不允许前端自说自话置 human_active）
    csMock.claimConversation.mockResolvedValue({
      conversation_id: queueItem.conversation_id,
      handoff_state: "agent_offered",
      agent_id: "agent-1",
      already_claimed: true,
    });

    const claimButton = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "认领",
    );
    await act(async () => {
      claimButton!.click();
    });

    await waitFor(
      () => !!container.textContent?.includes("待接单"),
      "显示后端返回的 agent_offered 状态",
    );
    expect(container.textContent).not.toContain("排队中");
  });
});
