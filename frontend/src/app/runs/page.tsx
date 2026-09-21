"use client";

// 历史分析列表。

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { actionColor, fmtRelative } from "@/lib/format";
import type { AnalysisRunSummary } from "@/lib/types";

export default function RunsPage() {
  const [items, setItems] = useState<AnalysisRunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listRuns({ limit: 50 });
      setItems(res.items);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (id: string) => {
    await api.deleteRun(id).catch(() => undefined);
    setItems((prev) => prev.filter((i) => i.id !== id));
    setTotal((t) => Math.max(0, t - 1));
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-4 md:px-6 md:py-6">
      <h1 className="mb-3 text-lg font-semibold">
        历史分析 {total > 0 && <span className="text-sm font-normal text-[var(--muted)]">({total})</span>}
      </h1>

      {loading && items.length === 0 ? (
        <ul className="space-y-2">
          {[0, 1, 2].map((i) => (
            <li key={i} className="h-16 animate-pulse rounded-xl border border-[var(--border)] bg-[var(--surface)]" />
          ))}
        </ul>
      ) : items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--border)] px-4 py-10 text-center">
          <p className="text-sm text-[var(--muted)]">还没有分析记录</p>
          <Link
            href="/analyze"
            className="mt-4 inline-block rounded-lg bg-[var(--accent)] px-4 py-2 text-sm text-white"
          >
            去分析
          </Link>
        </div>
      ) : (
        <ul className="space-y-2">
          {items.map((run) => (
            <li
              key={run.id}
              className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3"
            >
              <Link
                href={`/analyze?symbol=${run.symbol}&run=${run.id}`}
                className="min-w-0 flex-1"
              >
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{run.name || run.symbol}</span>
                  <span className="tabular shrink-0 text-xs text-[var(--muted)]">{run.symbol}</span>
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--muted)]">
                  {run.horizon} · {fmtRelative(run.created_at)}
                  {run.elapsed_sec != null && ` · ${run.elapsed_sec}s`}
                  {run.status !== "done" && ` · ${statusLabel(run.status)}`}
                </div>
              </Link>

              {run.action && (
                <span
                  className={`shrink-0 rounded border px-2 py-1 text-xs font-medium ${actionColor(run.action)}`}
                >
                  {run.action}
                  {run.conviction != null && (
                    <span className="tabular ml-1 font-normal opacity-75">{run.conviction}</span>
                  )}
                </span>
              )}

              <button
                type="button"
                onClick={() => void remove(run.id)}
                aria-label="删除记录"
                className="shrink-0 px-1 text-[var(--muted)]"
              >
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function statusLabel(status: string): string {
  return (
    { pending: "排队中", running: "进行中", error: "失败", cancelled: "已取消" }[status] ?? status
  );
}
