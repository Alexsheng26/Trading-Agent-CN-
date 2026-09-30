"use client";

// 斐波那契回撤摘要。
// 图上已经画了价位线，这里只补图上看不出来的东西：
// 摆动方向、回撤深度、最近的支撑/阻力、以及画不进图的扩展目标位。

import { fmtNum } from "@/lib/format";
import type { FibAnalysis } from "@/lib/types";

export function FibPanel({
  fib,
  close,
  enabled,
  onToggle,
  decimals = 2,
}: {
  fib: FibAnalysis;
  close?: number | null;
  enabled: boolean;
  onToggle: () => void;
  /** 价格小数位：ETF 传 3 */
  decimals?: number;
}) {
  const up = fib.direction === "上升";
  const pct = fib.retracement_pct ?? 0;
  const extensions = fib.levels.filter((l) => l.kind === "extension");

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">斐波那契回撤</span>
          <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">
            近 {fib.lookback} 日
          </span>
        </div>
        <button
          type="button"
          onClick={onToggle}
          aria-pressed={enabled}
          className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${
            enabled
              ? "bg-[var(--accent)] text-white"
              : "bg-[var(--surface-2)] text-[var(--muted)]"
          }`}
        >
          {enabled ? "已叠加到图上" : "叠加到图上"}
        </button>
      </div>

      <div className="mt-3 text-xs leading-relaxed text-[var(--muted)]">
        <span className={up ? "text-[var(--up)]" : "text-[var(--down)]"}>
          {fib.direction}段
        </span>
        ：
        {up ? (
          <>
            低点 <span className="tabular">{fmtNum(fib.swing_low, decimals)}</span>
            {fib.swing_low_date && `（${fib.swing_low_date}）`} → 高点{" "}
            <span className="tabular">{fmtNum(fib.swing_high, decimals)}</span>
            {fib.swing_high_date && `（${fib.swing_high_date}）`}
          </>
        ) : (
          <>
            高点 <span className="tabular">{fmtNum(fib.swing_high, decimals)}</span>
            {fib.swing_high_date && `（${fib.swing_high_date}）`} → 低点{" "}
            <span className="tabular">{fmtNum(fib.swing_low, decimals)}</span>
            {fib.swing_low_date && `（${fib.swing_low_date}）`}
          </>
        )}
      </div>

      {/* 回撤深度条：0% = 仍在该段末端，100% = 已回到起点 */}
      <div className="mt-3">
        <div className="flex items-baseline justify-between text-[11px] text-[var(--muted)]">
          <span>已回撤</span>
          <span className="tabular font-medium text-[var(--text)]">
            {pct.toFixed(1)}%
          </span>
        </div>
        <div className="relative mt-1.5 h-1.5 rounded-full bg-[var(--surface-2)]">
          <div
            className="h-full rounded-full bg-[var(--accent)]"
            style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
          />
          {/* 61.8% 黄金分割标记 */}
          <span
            className="absolute top-[-3px] h-[12px] w-px bg-[var(--warn)]"
            style={{ left: "61.8%" }}
            title="61.8% 黄金分割位"
          />
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <LevelCell
          decimals={decimals}
          label={up ? "下方支撑" : "下方支撑"}
          level={fib.nearest_support}
          close={close}
          tone="down"
        />
        <LevelCell
          decimals={decimals}
          label="上方阻力"
          level={fib.nearest_resistance}
          close={close}
          tone="up"
        />
      </div>

      {extensions.length > 0 && (
        <div className="mt-3 border-t border-[var(--border)] pt-3">
          <div className="text-[11px] text-[var(--muted)]">
            {up ? "上攻目标位" : "下跌目标位"}（扩展，未画在图上）
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
            {extensions.map((l) => (
              <span key={l.label} className="tabular text-[var(--muted)]">
                {l.label}{" "}
                <span className="font-medium text-[var(--text)]">
                  {fmtNum(l.price, decimals)}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      <p className="mt-3 text-[10px] leading-relaxed text-[var(--muted)]">
        摆动端点取自窗口内最高/最低价，会随新极值滚动，因此回撤率恒在 0-100%
        之间。是否突破区间请看形态标签里的 60 日新高/新低。
      </p>
    </div>
  );
}

function LevelCell({
  label,
  level,
  close,
  tone,
  decimals,
}: {
  label: string;
  level?: { label: string; price: number } | null;
  close?: number | null;
  tone: "up" | "down";
  decimals: number;
}) {
  const color = tone === "up" ? "text-[var(--up)]" : "text-[var(--down)]";
  const delta =
    level && close ? ((level.price / close - 1) * 100).toFixed(2) : null;

  return (
    <div className="rounded-lg bg-[var(--surface-2)] px-3 py-2">
      <div className="text-[11px] text-[var(--muted)]">{label}</div>
      {level ? (
        <>
          <div className={`tabular mt-0.5 text-sm font-medium ${color}`}>
            {fmtNum(level.price, decimals)}
          </div>
          <div className="tabular text-[10px] text-[var(--muted)]">
            {level.label}
            {delta !== null && ` · ${Number(delta) > 0 ? "+" : ""}${delta}%`}
          </div>
        </>
      ) : (
        <div className="mt-0.5 text-sm text-[var(--muted)]">—</div>
      )}
    </div>
  );
}
