"use client";

// 股票搜索框。支持代码和名称，输入防抖 300ms。

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { StockBasic } from "@/lib/types";

export function StockSearch({
  onPick,
  placeholder = "输入代码或名称，如 600519 / 茅台",
  autoFocus = false,
}: {
  onPick: (stock: StockBasic) => void;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StockBasic[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const keyword = query.trim();
    if (keyword.length < 1) {
      setResults([]);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);

    // 前端也要兜住超时：后端虽然已给数据源加了时间盒，但网络层面
    // 仍可能卡住。没有这层保护时输入框会永远停在"搜索中…"。
    const controller = new AbortController();
    const killer = setTimeout(() => controller.abort(), 15000);

    const timer = setTimeout(() => {
      api
        .search(keyword, 12, controller.signal)
        .then((r) => {
          setResults(r);
          setOpen(true);
        })
        .catch((err: unknown) => {
          setResults([]);
          setOpen(true);
          setError(
            err instanceof DOMException && err.name === "AbortError"
              ? "搜索超时。数据源可能不可用 —— 可以直接输入完整 6 位代码。"
              : err instanceof Error
                ? err.message
                : "搜索失败",
          );
        })
        .finally(() => {
          clearTimeout(killer);
          setLoading(false);
        });
    }, 300);

    return () => {
      clearTimeout(timer);
      clearTimeout(killer);
      controller.abort();
    };
  }, [query]);

  // 点击外部关闭下拉
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const pick = (stock: StockBasic) => {
    onPick(stock);
    setQuery("");
    setResults([]);
    setOpen(false);
  };

  return (
    <div ref={boxRef} className="relative">
      <input
        type="search"
        inputMode="search"
        autoFocus={autoFocus}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        placeholder={placeholder}
        className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-4 py-3 text-[15px] outline-none placeholder:text-[var(--muted)] focus:border-[var(--accent)]"
      />
      {loading && (
        <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[var(--muted)]">
          搜索中…
        </span>
      )}

      {open && results.length > 0 && (
        <ul className="absolute left-0 right-0 top-full z-30 mt-1 max-h-72 overflow-auto rounded-xl border border-[var(--border)] bg-[var(--surface)] shadow-xl">
          {results.map((stock) => (
            <li key={stock.symbol}>
              <button
                type="button"
                onClick={() => pick(stock)}
                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-[var(--surface-2)]"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">
                    {stock.name || stock.symbol.split(".")[0]}
                    {!stock.name && (
                      <span className="ml-2 text-[10px] font-normal text-[var(--muted)]">
                        代码直达（数据源未返回名称）
                      </span>
                    )}
                  </span>
                  <span className="tabular block text-xs text-[var(--muted)]">
                    {stock.symbol}
                    {stock.industry ? ` · ${stock.industry}` : ""}
                  </span>
                </span>
                {stock.board && (
                  <span className="shrink-0 rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">
                    {stock.board}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      {open && !loading && query.trim() && results.length === 0 && (
        <div
          className={`absolute left-0 right-0 top-full z-30 mt-1 rounded-xl border px-4 py-3 text-xs ${
            error
              ? "border-[var(--warn)] bg-[var(--warn-soft)] text-[var(--warn)]"
              : "border-[var(--border)] bg-[var(--surface)] text-[var(--muted)]"
          }`}
        >
          {error ?? "没找到匹配的股票。试试完整代码，如 600519。"}
        </div>
      )}
    </div>
  );
}
