// cs-workstation-v2 P0：坐席工作台壳导航（IA 对照 prototypes/agent-workstation.html 侧栏）。
// P8 路由切换：/cs2/* → /cs/*（src/cs2 组件命名空间保留不动）。
// supervisorOnly 仅为目标态占位，角色隐藏逻辑随 P3 视角切换落地。
export interface Cs2NavItem {
  href: string;
  label: string;
  supervisorOnly?: boolean;
}

export const CS2_NAV_ITEMS: Cs2NavItem[] = [
  { href: "/cs/workspace", label: "工作台" },
  { href: "/cs/conversations", label: "会话管理" },
  { href: "/cs/tickets", label: "工单管理" },
  { href: "/cs/refunds", label: "退款审核", supervisorOnly: true },
  { href: "/cs/qa", label: "质检中心" },
  { href: "/cs/dashboard", label: "数据看板", supervisorOnly: true },
  { href: "/cs/knowledge", label: "知识助手" },
];
