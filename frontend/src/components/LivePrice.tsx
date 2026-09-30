"use client";

// 个股实时价格头部：大号价格 + 时段状态 + 开高低收。
//
// 设计上的两个刻意选择：
//   1. 价格变动时闪一下背景。盯盘时数字静静地变掉是看不见的。
//   2. 明确写出"现在在不在刷新、上次刷新是几点"。看行情最怕的是
//      把一个半小时前的死数据当成实时价 —— 宁可显眼地说"已收盘"。

import { useEffect, useRef, useState } from "react";
import { useLiveQuote, useTicker } from "@/lib/useLiveQuote";
import {
  changeColor,
  fmtClock,
  fmtCountdown,
  fmtNum,
  fmtPct,
  fmtSigned,
  phaseColor,
} from "@/lib/format";
import { priceDecimals } from "@/lib/symbols";
import type { Quote, SessionInfo } from "@/lib/types";

interface Props {
  symbol: string;
  /** 数据源没返回名称时的兜底（分析流里可能已经拿到了） */
  fallbackName?: string;
  live: ReturnType<typeof useLiveQuote>;
  /** 头部右侧的额外内容，例如斐波那契标签 */
  children?: React.ReactNode;
}

export function LivePrice({ symbol, fallbackName, live, children }: Props) {
  const { quote, session, polling, updatedAt, loading, error, tick, refresh } = live;

  const change = changeAmount(quote);
  const color = changeColor(quote?.change_pct);
  // ETF 三位小数：两位的话 0.873 和 0.878 看起来一样
  const digits = priceDecimals(symbol, quote);
  // seq 变一次就重放一次动画：靠 key 强制 React 重建节点，不然动画只播第一次
  const flash = tick.seq > 0 ? (tick.dir > 0 ? "flash-up" : "flash-down") : "";

  return (
    <div className="mb-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg font-semibold">{quote?.name || fallbackName || symbol}</h1>
        <span className="tabular text-sm text-[var(--muted)]">{symbol}</span>
        <SessionBadge session={session} polling={polling} stale={quote?.is_realtime === false} />
        {/* 非实时价必须显著标注。盘中把上一个交易日的收盘价当现价展示
            而不告知，比没有价格危险得多 —— 用户会照着它下单。 */}
        {quote && quote.is_realtime === false && (
          <span className="rounded bg-[var(--warn-soft)] px-1.5 py-0.5 text-[10px] text-[var(--warn)]">
            收盘价{quote.quote_date ? ` · ${quote.quote_date.slice(5)}` : ""}（非实时）
          </span>
        )}
        {children}
      </div>

      <div className="mt-2 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        {/* ---- 价格区 ---- */}
        <div key={tick.seq} className={`-mx-1 rounded-lg px-1 ${flash}`}>
          <div className="flex flex-wrap items-baseline gap-x-3">
            {/* 大号独立数字用比例字体：等宽在这个字号下显得松散 */}
            <span className={`text-[2.25rem] font-semibold leading-none ${color}`}>
              {quote ? fmtNum(quote.price, digits) : "—"}
            </span>
            <span className={`tabular text-base font-medium ${color}`}>
              {fmtSigned(change, digits)}
            </span>
            <span className={`tabular text-base font-medium ${color}`}>
              {fmtPct(quote?.change_pct)}
            </span>
          </div>
        </div>

        {/* ---- 开高低收 ---- */}
        <dl className="grid shrink-0 grid-cols-4 gap-x-4 gap-y-1 text-[11px] md:gap-x-5">
          <Field label="今开" value={fmtNum(quote?.open, digits)} base={quote?.prev_close} raw={quote?.open} />
          <Field label="最高" value={fmtNum(quote?.high, digits)} base={quote?.prev_close} raw={quote?.high} />
          <Field label="最低" value={fmtNum(quote?.low, digits)} base={quote?.prev_close} raw={quote?.low} />
          <Field label="昨收" value={fmtNum(quote?.prev_close, digits)} />
        </dl>
      </div>

      {/* ---- 刷新状态 ---- */}
      <div className="mt-2 flex items-center gap-2 text-[11px] text-[var(--muted)]">
        {error ? (
          <span className="text-[var(--warn)]">行情刷新失败：{error}</span>
        ) : quote && quote.is_realtime === false ? (
          <span>
            实时行情接口当前不可用，显示的是
            {quote.quote_date ? ` ${quote.quote_date} ` : "最近一个交易日"}
            的收盘价。去「设置 → 数据源诊断」看哪一步失败。
          </span>
        ) : updatedAt ? (
          <span className="tabular">
            {polling ? "自动刷新 · " : ""}
            更新于 {fmtClock(updatedAt)}
          </span>
        ) : (
          <span>{loading ? "读取行情…" : "暂无行情"}</span>
        )}
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="ml-auto shrink-0 rounded border border-[var(--border)] px-2 py-0.5 text-[11px] text-[var(--muted)] transition-colors hover:text-[var(--text)] disabled:opacity-50"
        >
          {loading ? "刷新中…" : "手动刷新"}
        </button>
      </div>
    </div>
  );
}

function changeAmount(quote: Quote | null): number | null {
  if (!quote || quote.prev_close == null) return null;
  return quote.price - quote.prev_close;
}

/** 开高低收的单格。相对昨收着色 —— 今开低于昨收就该是绿的。 */
function Field({
  label,
  value,
  base,
  raw,
}: {
  label: string;
  value: string;
  base?: number | null;
  raw?: number | null;
}) {
  const tone =
    base != null && raw != null ? changeColor(raw - base) : "text-[var(--text)]";
  return (
    <div className="text-right">
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className={`tabular mt-0.5 ${tone}`}>{value}</dd>
    </div>
  );
}

/**
 * 时段徽标。
 * 交易中显示呼吸点和刷新间隔；非交易时段显示距下次开盘还有多久 ——
 * 这比干巴巴一个"已收盘"有用得多。
 */
export function SessionBadge({
  session,
  polling,
  stale = false,
}: {
  session: SessionInfo | null;
  polling: boolean;
  /** 手里的价格是不是"非实时"的兜底价 */
  stale?: boolean;
}) {
  // 倒计时每 30 秒走一次就够了（显示精度到分钟）
  const now = useTicker(30_000, session !== null && !session.is_live);
  if (!session) return null;

  const detail = (() => {
    if (session.is_live) {
      if (!polling) return "已暂停刷新";
      // 盘中却只有兜底的收盘价时，不能说"5 秒自动刷新" ——
      // 那会让人以为看到的数字每 5 秒在更新，而它其实是一个不动的收盘价。
      // 轮询确实在继续（等着实时接口恢复），所以说"重试中"才准确。
      return stale
        ? `每 ${session.poll_interval ?? 5} 秒重试实时接口`
        : `${session.poll_interval ?? 5} 秒自动刷新`;
    }
    if (!session.next_change) return "";
    const ms = Date.parse(session.next_change) - now;
    if (Number.isNaN(ms)) return "";
    // 午间休市是"恢复"而不是"开盘"，其余时段都是等下一次开盘
    const verb = session.phase === "午间休市" ? "距恢复" : "距开盘";
    return `${verb} ${fmtCountdown(ms)}`;
  })();

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px] ${phaseColor(session.phase)}`}
      title={session.note}
    >
      {session.is_live && polling && (
        <span className="live-dot h-1.5 w-1.5 rounded-full bg-current" />
      )}
      <span>{session.phase}</span>
      {detail && <span className="opacity-70">· {detail}</span>}
    </span>
  );
}

/**
 * 会闪烁的价格数字，给自选股列表用。
 * 每个实例自己记住上一次的值 —— 列表里哪一行动了一眼就能看出来。
 */
export function TickPrice({
  value,
  digits = 2,
  className = "",
}: {
  value?: number | null;
  /** 小数位：ETF 传 3 */
  digits?: number;
  className?: string;
}) {
  const [state, setState] = useState<{ dir: 1 | 0 | -1; seq: number }>({ dir: 0, seq: 0 });
  const prev = useRef<number | null | undefined>(undefined);

  useEffect(() => {
    const before = prev.current;
    prev.current = value;
    if (before == null || value == null || before === value) return;
    setState((s) => ({ dir: value > before ? 1 : -1, seq: s.seq + 1 }));
  }, [value]);

  const flash = state.seq > 0 ? (state.dir > 0 ? "flash-up" : "flash-down") : "";
  return (
    <span key={state.seq} className={`tabular inline-block rounded px-1 ${flash} ${className}`}>
      {fmtNum(value, digits)}
    </span>
  );
}
