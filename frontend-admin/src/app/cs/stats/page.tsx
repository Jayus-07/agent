"use client";

/**
 * /cs/stats — 智能客服满意度统计（014_cs_rating 需求闭环）
 *
 * 数据源：GET /api/cs/conversations/stats（后端 cs_admin.py 聚合）
 * 展示：统计卡（会话/消息/均分/评价数/转人工）+ 满意度分布 + 意图分布
 */
import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  Headphones, MessageSquare, Star, Headset, RefreshCw, ArrowLeft,
} from "lucide-react";
import { getCSStats } from "@/api/cs";
import type { CSStatsResponse } from "@/types/cs";

const RATING_COLORS: Record<number, string> = {
  5: "#16a34a",
  4: "#65a30d",
  3: "#d97706",
  2: "#ea580c",
  1: "#dc2626",
};

export default function CSStatsPage() {
  const [stats, setStats] = useState<CSStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStats(await getCSStats());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  const maxRating = Math.max(
    1,
    ...Object.values(stats?.rating_dist || {}).map(Number),
  );
  const maxIntent = Math.max(1, ...(stats?.intent_dist || []).map((i) => i.count));

  return (
    <div className="flex-1 overflow-auto bg-surface-base">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">
        {/* Header */}
        <div className="flex items-center gap-3">
          <Link
            href="/cs/conversations"
            className="text-text-muted hover:text-accent transition-colors"
            title="返回会话管理"
          >
            <ArrowLeft size={18} />
          </Link>
          <div className="w-9 h-9 rounded-xl bg-accent/10 flex items-center justify-center">
            <Headphones size={18} className="text-accent" />
          </div>
          <div className="flex-1">
            <h1 className="text-lg font-semibold text-text-primary">满意度统计</h1>
            <p className="text-xs text-text-muted">客服会话量、满意度评分与意图分布总览</p>
          </div>
          <button
            onClick={fetchStats}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border-subtle bg-white text-text-secondary hover:text-text-primary hover:border-accent/40 transition-colors"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            刷新
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
            加载失败: {error}
          </div>
        )}

        {loading && !stats && (
          <div className="text-sm text-text-muted py-12 text-center">加载中…</div>
        )}

        {stats && (
          <>
            {/* 统计卡片 */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
              <StatCard
                icon={<Headphones size={16} />}
                label="总会话数"
                value={stats.session_count}
                color="#0284c7"
              />
              <StatCard
                icon={<MessageSquare size={16} />}
                label="总消息数"
                value={stats.message_count}
                color="#7c3aed"
              />
              <StatCard
                icon={<Star size={16} />}
                label="平均满意度"
                value={stats.rated_count > 0 ? `${stats.avg_rating} / 5` : "暂无评价"}
                color="#d97706"
              />
              <StatCard
                icon={<Star size={16} />}
                label="评价数"
                value={stats.rated_count}
                color="#16a34a"
              />
              <StatCard
                icon={<Headset size={16} />}
                label="转人工会话"
                value={stats.handoff_count}
                color="#dc2626"
              />
            </div>

            <div className="grid md:grid-cols-2 gap-4">
              {/* 满意度分布 */}
              <div className="bg-white border border-border-subtle rounded-xl p-4">
                <h3 className="text-sm font-semibold text-text-primary mb-3">满意度分布（1-5 星）</h3>
                {stats.rated_count > 0 ? (
                  <div className="space-y-2.5">
                    {[5, 4, 3, 2, 1].map((star) => {
                      const count = Number(stats.rating_dist[String(star)] || 0);
                      return (
                        <div key={star} className="flex items-center gap-3">
                          <span className="text-xs w-14 text-text-secondary shrink-0">{star} 星</span>
                          <div className="flex-1 h-4 rounded bg-slate-100 overflow-hidden">
                            <div
                              className="h-full rounded transition-all duration-500"
                              style={{
                                width: `${(count / maxRating) * 100}%`,
                                backgroundColor: RATING_COLORS[star],
                              }}
                            />
                          </div>
                          <span className="text-xs w-8 text-right text-text-muted">{count}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-text-muted py-6 text-center">
                    暂无评价数据 —— 用户在客服窗口对话后会看到评分卡
                  </p>
                )}
              </div>

              {/* 意图分布 */}
              <div className="bg-white border border-border-subtle rounded-xl p-4">
                <h3 className="text-sm font-semibold text-text-primary mb-3">意图分布（Top 6）</h3>
                {stats.intent_dist.length > 0 ? (
                  <div className="space-y-2.5">
                    {stats.intent_dist.map((item) => (
                      <div key={item.name} className="flex items-center gap-3">
                        <span className="text-xs w-20 truncate text-text-secondary shrink-0" title={item.name}>
                          {item.name}
                        </span>
                        <div className="flex-1 h-4 rounded bg-slate-100 overflow-hidden">
                          <div
                            className="h-full rounded bg-accent transition-all duration-500"
                            style={{ width: `${(item.count / maxIntent) * 100}%` }}
                          />
                        </div>
                        <span className="text-xs w-8 text-right text-text-muted">{item.count}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-text-muted py-6 text-center">暂无意图识别数据</p>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function StatCard({
  icon, label, value, color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  color: string;
}) {
  return (
    <div className="bg-white border border-border-subtle rounded-xl p-4 flex items-center gap-3">
      <div
        className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
        style={{ backgroundColor: `${color}14`, color }}
      >
        {icon}
      </div>
      <div className="min-w-0">
        <div className="text-[11px] text-text-muted">{label}</div>
        <div className="text-lg font-semibold text-text-primary truncate">{value}</div>
      </div>
    </div>
  );
}
