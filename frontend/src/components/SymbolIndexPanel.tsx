"use client";

// 本地代码目录：按名称搜索的底座。
//
// 这块界面的意义在于把"为什么搜不到名字"变成一个用户能自己看懂、自己解决
// 的问题：目录有多少条、什么时候建的、按一下就能重建。

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import type { SymbolIndexStatus } from "@/lib/types";

export function SymbolIndexPanel() {
  const [status, setStatus] = useState<SymbolIndexStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await api.symbolsStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取目录状态失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rebuild = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      // wait=true：让用户明确看到"建好了"或"失败了"，而不是一个模糊的"已开始"
      setStatus(await api.refreshSymbols(true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "重建失败");
      void load();
    } finally {
      setBusy(false);
    }
  }, [load]);

  const ready = (status?.count ?? 0) > 0;
  const etfCount = status?.etf_count ?? 0;

  return (
    <div className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">
              {status === null
                ? "读取中…"
                : ready
                  ? `${status.count - etfCount} 只股票${etfCount ? ` · ${etfCount} 只 ETF` : ""}`
                  : "尚未建立"}
            </span>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                ready
                  ? "bg-[var(--down-soft)] text-[var(--down)]"
                  : "bg-[var(--warn-soft)] text-[var(--warn)]"
              }`}
            >
              {ready ? "按名称搜索可用" : "只能按代码搜索"}
            </span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--muted)]">
            {status?.updated_at
              ? `更新于 ${fmtRelative(status.updated_at)}`
              : "启动时会自动在后台建立"}
            {status?.sources.length ? ` · 来源 ${status.sources.join("、")}` : ""}
            {status?.stale && ready ? " · 已过期，会自动补新股" : ""}
            {status?.refreshing ? " · 正在后台重建" : ""}
          </p>
          {ready && etfCount === 0 && !status?.refreshing && (
            // 老版本建的目录里没有 ETF。启动时会自动补；补不上说明 ETF 列表接口不通
            <p className="mt-1 text-[11px] text-[var(--warn)]">
              {"目录里还没有 ETF，按名称搜不到 ETF（按代码仍可以）。点右边「重建目录」补上；"}
              {"重建后仍为 0，去下面的数据源诊断看 ETF 相关几行。"}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => void rebuild()}
          disabled={busy}
          className="shrink-0 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs disabled:opacity-50"
        >
          {busy ? "重建中…" : "重建目录"}
        </button>
      </div>

      {error && (
        <div className="mt-3 rounded-lg bg-[var(--up-soft)] px-3 py-2 text-[11px] leading-relaxed text-[var(--up)]">
          {error}
          <br />
          目录建不起来说明数据源当前取不到全市场列表 —— 用下面的诊断看是哪一步失败。
        </div>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-[var(--muted)]">
        按名称搜索需要全市场代码表。以前每次搜索都现场去问数据源，数据源慢或挂
        就会一直卡在「搜索中」；现在成功拉到一次就存在本地，之后搜索是纯本地查询，
        <strong className="text-[var(--text)]">数据源事后挂掉也照样能搜</strong>。
      </p>
    </div>
  );
}
