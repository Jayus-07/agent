// cs-workstation-v2 P0：/cs2/* 路由壳导航（IA 对照 prototypes/agent-workstation.html 侧栏）。
// supervisorOnly 仅为目标态占位，角色隐藏逻辑随 P3 视角切换落地。
export interface Cs2NavItem {
  href: string;
  label: string;
  supervisorOnly?: boolean;
}

export const CS2_NAV_ITEMS: Cs2NavItem[] = [
  { href: "/cs2/workspace", label: "工作台" },
  { href: "/cs2/conversations", label: "会话管理" },
  { href: "/cs2/tickets", label: "工单管理" },
  { href: "/cs2/refunds", label: "退款审核", supervisorOnly: true },
  { href: "/cs2/qa", label: "质检中心" },
  { href: "/cs2/dashboard", label: "数据看板", supervisorOnly: true },
  { href: "/cs2/knowledge", label: "知识助手" },
];
