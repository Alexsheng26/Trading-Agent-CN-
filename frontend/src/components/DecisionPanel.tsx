"use client";

import { actionColor, fmtNum } from "@/lib/format";
import type { Decision } from "@/lib/types";

export function DecisionPanel({ decision }: { decision: Decision }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 md:p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`rounded-lg border px-3 py-1.5 text-lg font-semibold ${actionColor(decision.action)}`}
        >
          {decision.action}
        </span>
        <div className="text-xs text-[var(--muted)]">
          <div>
            信心度 <span className="tabular font-medium text-[var(--text)]">{decision.conviction}</span>/100
          </div>
          <div className="mt-0.5">
            建议仓位{" "}
            <span className="tabular font-medium text-[var(--text)]">
              {fmtNum(decision.position_pct, 1)}%
            </span>
          </div>
        </div>
        <div className="ml-auto flex gap-2 text-[11px]">
          <Tag label={`周期 ${decision.horizon}`} />
          <Tag
            label={`数据${decision.data_quality}`}
            tone={decision.data_quality === "完整" ? "normal" : "warn"}
          />
        </div>
      </div>

      {/* 信心度条：低信心一眼看得出 */}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
        <div
          className="h-full rounded-full bg-[var(--accent)] transition-all"
          style={{ width: `${Math.max(2, decision.conviction)}%` }}
        />
      </div>

      {decision.summary && (
        <p className="mt-4 text-sm leading-relaxed">{decision.summary}</p>
      )}

      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <PriceCell label="建议区间" value={decision.entry_zone} />
        <PriceCell label="止损" value={decision.stop_loss} tone="down" />
        <PriceCell label="止盈" value={decision.take_profit} tone="up" />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <ReasonList title="核心理由" items={decision.key_reasons} tone="up" />
        <ReasonList title="主要风险" items={decision.key_risks} tone="down" />
      </div>

      {decision.dissent && (
        <div className="mt-4 rounded-lg bg-[var(--warn-soft)] p-3">
          <div className="text-[11px] font-medium text-[var(--warn)]">反面提醒（未被采纳的意见）</div>
          <p className="mt-1 text-xs leading-relaxed text-[var(--text)]">{decision.dissent}</p>
        </div>
      )}

      {decision.disclaimer && (
        <p className="mt-4 border-t border-[var(--border)] pt-3 text-[11px] leading-relaxed text-[var(--muted)]">
          {decision.disclaimer.replace(/\*\*/g, "")}
        </p>
      )}
    </div>
  );
}

function Tag({ label, tone = "normal" }: { label: string; tone?: "normal" | "warn" }) {
  const cls =
    tone === "warn"
      ? "bg-[var(--warn-soft)] text-[var(--warn)]"
      : "bg-[var(--surface-2)] text-[var(--muted)]";
  return <span className={`rounded px-2 py-1 ${cls}`}>{label}</span>;
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
    tone === "up" ? "text-[var(--up)]" : tone === "down" ? "text-[var(--down)]" : "text-[var(--text)]";
  return (
    <div className="rounded-lg bg-[var(--surface-2)] px-3 py-2">
      <div className="text-[11px] text-[var(--muted)]">{label}</div>
      <div className={`tabular mt-0.5 text-sm font-medium ${color}`}>{value || "—"}</div>
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
    <div>
      <div className="text-[11px] font-medium text-[var(--muted)]">{title}</div>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-[var(--muted)]">—</p>
      ) : (
        <ul className="mt-1.5 space-y-1.5">
          {items.map((item, i) => (
            <li key={i} className="flex gap-2 text-xs leading-relaxed">
              <span className={`mt-1.5 h-1 w-1 shrink-0 rounded-full ${dot}`} />
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
