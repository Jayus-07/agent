"use client";

/**
 * /cs — 坐席工作台首页（客服端 MVP）
 *
 * 2026-09-21 三端拆分新增页。原管理端 navConfig 的「客服对话」指向 /cs
 * 却没有 page.tsx（死链 404），这里补上：概览待接入工单数 + 三个功能入口。
 *
 * 数据来源：GET /cs/conversations/handoff/queue（与 /cs/handoff 同源，
 * 仅取 waiting_human 计数；失败时显式降级为「—」，不阻塞入口导航）。
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, BarChart3, BellRing, Headphones, Headset, MessagesSquare } from "lucide-react";
import { getHandoffQueue } from "@/api/cs";

const ENTRIES = [
  {
    href: "/cs/handoff",
    label: "人工接入坐席",
    desc: "待接单、认领、与客户实时对话",
    icon: Headset,
  },
  {
    href: "/cs/conversations",
    label: "会话管理",
    desc: "客服对话记录与链路追踪",
    icon: MessagesSquare,
  },
  {
    href: "/cs/stats",
    label: "满意度统计",
    desc: "会话量、评分与意图分布总览",
    icon: BarChart3,
  },
];

export default function CSHomePage() {
  const [waiting, setWaiting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getHandoffQueue()
      .then((res) => {
        if (!alive) return;
        setWaiting(
          res.items.filter((i) => i.handoff_state === "waiting_human").length,
        );
      })
      .catch((e) => {
        if (alive) setError((e as Error).message);
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="flex-1 overflow-auto bg-surface-base">
      <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-accent/10 flex items-center justify-center">
            <Headphones size={20} className="text-accent" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-text-primary">坐席工作台</h1>
            <p className="text-xs text-text-muted">智能客服 — 会话接入与服务质量</p>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
            待接入数加载失败: {error}
          </div>
        )}

        <div className="bg-white border border-border-subtle rounded-xl p-4 flex items-center gap-4">
          <div className="w-9 h-9 rounded-lg bg-red-50 flex items-center justify-center shrink-0">
            <BellRing size={16} className="text-red-500" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs text-text-muted">当前待接入工单</div>
            <div className="text-xl font-semibold text-text-primary">
              {waiting === null ? "—" : waiting}
            </div>
          </div>
          <Link
            href="/cs/handoff"
            className="px-3 py-1.5 text-xs rounded-lg bg-accent text-white hover:opacity-90 transition-opacity"
          >
            去处理
          </Link>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {ENTRIES.map((e) => {
            const Icon = e.icon;
            return (
              <Link
                key={e.href}
                href={e.href}
                className="group bg-white border border-border-subtle rounded-xl p-4
                  hover:border-accent/40 transition-colors"
              >
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center">
                    <Icon size={15} className="text-accent" />
                  </div>
                  <span className="text-sm font-medium text-text-primary">{e.label}</span>
                  <ArrowRight
                    size={14}
                    className="ml-auto text-text-muted group-hover:text-accent transition-colors"
                  />
                </div>
                <p className="text-xs text-text-muted">{e.desc}</p>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
