"use client";

import { useState, useEffect } from "react";
import { getMultiQueryMode, setMultiQueryMode } from "@/lib/api";

const LABELS: Record<string, string> = {
  auto: "多查询:自动",
  on: "多查询:开",
  off: "多查询:关",
};

const NEXT: Record<string, string> = {
  auto: "on",
  on: "off",
  off: "auto",
};

export default function MultiQueryToggle() {
  const [mode, setMode] = useState<string>("auto");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getMultiQueryMode()
      .then((r) => setMode(r.mode))
      .catch(() => {});
  }, []);

  const toggle = async () => {
    if (loading) return;
    const next = NEXT[mode] || "auto";
    setLoading(true);
    try {
      await setMultiQueryMode(next);
      setMode(next);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={toggle}
      disabled={loading}
      className={`text-[11px] px-2 py-1 rounded-full border transition-colors ${
        mode === "off"
          ? "border-gray-300 text-gray-400 bg-gray-50"
          : mode === "on"
          ? "border-blue-400 text-blue-600 bg-blue-50"
          : "border-green-400 text-green-600 bg-green-50"
      }`}
    >
      {LABELS[mode] || mode}
    </button>
  );
}
