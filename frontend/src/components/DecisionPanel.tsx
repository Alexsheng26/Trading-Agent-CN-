"use client";

// 交易决策面板。
//
// 这是整个页面要回答的问题（"我该怎么办"），所以给它主角待遇：
// 操作建议用 hero 级字号，信心度/仓位/周期做成并排的统计块。
// 一个视图只应有一个 hero 数字，所以顶部的股价降为次级信息。
//
// 大号数字刻意**不用**等宽字体：tabular-nums 让每位数字占 "0" 的宽度，
// 大字号下显得松散；等宽留给需要纵向对齐的表格。

import { actionColor, fmtNum } from "@/lib/format";
import type { Decision } from "@/lib/types";

export function DecisionPanel({ decision }: { decision: Decision }) {
  const cls = actionColor(decision.action);

  return (
    <section
      className={`overflow-hidden rounded-2xl border bg-[var(--surface)] ${cls.split(" ").find((c) => c.startsWith("border-")) ?? "border-[var(--border)]"}`}
    >
      {/* ---------- 主结论 ---------- */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-4 p-4 md:p-5">
        <div className="min-w-0">
          <div className="text-[11px] text-[var(--muted)]">操作建议</div>
          <div
            className={`mt-0.5 text-4xl font-semibold leading-none tracking-tight md:text-5xl ${cls.split(" ").find((c) => c.startsWith("text-")) ?? ""}`}
          >
            {decision.action}
          </div>
        </div>

        {/* 信心度量表。轨道用同色系浅色阶而非中性灰，
            这样低信心也能一眼看出"条很短"而不是"没画" */}
        <div className="min-w-[9rem] flex-1">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] text-[var(--muted)]">信心度</span>
            <span className="text-sm font-semibold">{decision.conviction}
              <span className="text-[11px] font-normal text-[var(--muted)]">/100</span>
            </span>
          </div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-[var(--accent-soft)]">
            <div
              className="h-full rounded-full bg-[var(--accent)] transition-all duration-700"
              style={{ width: `${Math.max(2, decision.conviction)}%` }}
            />
          </div>
          {decision.conviction < 40 && (
            <div className="mt-1 text-[10px] text-[var(--warn)]">
              信心度偏低，建议当作「暂不行动」的参考
            </div>
          )}
        </div>

        <div className="flex gap-2">
          <MiniStat label="建议仓位" value={`${fmtNum(decision.position_pct, 1)}%`} />
          <MiniStat label="周期" value={decision.horizon} />
          <MiniStat
            label="数据质量"
            value={decision.data_quality || "—"}
            tone={decision.data_quality === "完整" ? "normal" : "warn"}
          />
        </div>
      </div>

      {decision.summary && (
        <p className="border-t border-[var(--border)] px-4 py-3 text-sm leading-relaxed md:px-5">
          {decision.summary}
        </p>
      )}

      {/* ---------- 价位 ---------- */}
      <div className="grid grid-cols-1 gap-px border-t border-[var(--border)] bg-[var(--border)] sm:grid-cols-3">
        <PriceCell label="建议区间" value={decision.entry_zone} />
        <PriceCell label="止损" value={decision.stop_loss} tone="down" />
        <PriceCell label="止盈" value={decision.take_profit} tone="up" />
      </div>

      {/* ---------- 理由与风险 ---------- */}
      <div className="grid grid-cols-1 gap-px bg-[var(--border)] md:grid-cols-2">
        <ReasonList title="核心理由" items={decision.key_reasons} tone="up" />
        <ReasonList title="主要风险" items={decision.key_risks} tone="down" />
      </div>

      {decision.dissent && (
        <div className="bg-[var(--warn-soft)] px-4 py-3 md:px-5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-[var(--warn)]">
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <path d="M8 5.5v4M8 11.5h.01" />
              <circle cx="8" cy="8" r="6.5" />
            </svg>
            反面提醒（未被采纳的意见）
          </div>
          <p className="mt-1 text-xs leading-relaxed">{decision.dissent}</p>
        </div>
      )}

      {decision.fallback_note && (
        <p className="border-t border-[var(--border)] bg-[var(--warn-soft)] px-4 py-2 text-[11px] leading-relaxed text-[var(--warn)] md:px-5">
          {decision.fallback_note}
        </p>
      )}

      {decision.disclaimer && (
        <p className="border-t border-[var(--border)] px-4 py-3 text-[11px] leading-relaxed text-[var(--muted)] md:px-5">
          {decision.disclaimer.replace(/\*\*/g, "")}
        </p>
      )}
    </section>
  );
}

function MiniStat({
  label,
  value,
  tone = "normal",
}: {
  label: string;
  value: string;
  tone?: "normal" | "warn";
}) {
  return (
    <div className="rounded-lg bg-[var(--surface-2)] px-2.5 py-1.5">
      <div className="whitespace-nowrap text-[10px] text-[var(--muted)]">{label}</div>
      <div
        className={`mt-0.5 whitespace-nowrap text-[13px] font-medium ${
          tone === "warn" ? "text-[var(--warn)]" : ""
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function PriceCell({
  label,
  value,
  tone = "normal",
}: {
  label: string;
  value: string;
  tone?: "normal" | "up" | "down";
}) {
  const color =
    tone === "up"
      ? "text-[var(--up)]"
      : tone === "down"
        ? "text-[var(--down)]"
        : "text-[var(--text)]";
  return (
    <div className="bg-[var(--surface)] px-4 py-3 md:px-5">
      <div className="text-[11px] text-[var(--muted)]">{label}</div>
      <div className={`mt-0.5 text-sm font-medium leading-snug ${color}`}>
        {value || "—"}
      </div>
    </div>
  );
}

function ReasonList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "up" | "down";
}) {
  const dot = tone === "up" ? "bg-[var(--up)]" : "bg-[var(--down)]";
  return (
    <div className="bg-[var(--surface)] px-4 py-3 md:px-5">
      <div className="text-[11px] font-medium text-[var(--muted)]">{title}</div>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-[var(--muted)]">—</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {items.map((item, i) => (
            <li key={i} className="flex gap-2 text-xs leading-relaxed">
              <span className={`mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
              <span className="min-w-0">{item}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
