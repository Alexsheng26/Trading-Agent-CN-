"use client";

// 多智能体倾向一览。
//
// 为什么需要它：7 个智能体各写 300-600 字，倾向标签埋在各自卡片右上角，
// 想知道"整体偏多还是偏空"得一张张翻。这条把结论压成一行，
// 并给出多空对比 —— 分歧大不大是决策时最先要看的东西。

import { signalColor } from "@/lib/format";
import type { Stage } from "@/lib/types";
import type { LiveAgent } from "@/lib/useAnalysisStream";

const STAGE_LABEL: Record<string, string> = {
  analysts: "分析师",
  debate: "辩论",
  risk: "风控",
  decision: "决策",
};

export function SignalStrip({ agents }: { agents: Record<string, LiveAgent> }) {
  const rated = Object.values(agents).filter((a) => a.signal && !a.error);
  if (rated.length === 0) return null;

  const bull = rated.filter((a) => a.signal === "看多").length;
  const bear = rated.filter((a) => a.signal === "看空").length;
  const flat = rated.length - bull - bear;

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-sm font-medium">倾向一览</span>
        <span className="text-[11px] text-[var(--muted)]">
          已表态 {rated.length} 位 · 看多 {bull} / 中性 {flat} / 看空 {bear}
        </span>
      </div>

      {/* 多空对比条。三段之间留 2px 间隙，避免相邻色块糊成一片 */}
      <div className="mt-2.5 flex h-2 gap-[2px] overflow-hidden rounded-full">
        <Segment count={bull} total={rated.length} className="bg-[var(--up)]" label="看多" />
        <Segment count={flat} total={rated.length} className="bg-[var(--flat)]" label="中性" />
        <Segment count={bear} total={rated.length} className="bg-[var(--down)]" label="看空" />
      </div>

      <ul className="mt-3 space-y-1.5">
        {rated.map((a) => (
          <li key={a.key} className="flex items-center gap-2 text-xs">
            <span className="w-10 shrink-0 text-[10px] text-[var(--muted)]">
              {STAGE_LABEL[a.stage] ?? a.stage}
            </span>
            <span className="min-w-0 flex-1 truncate">{a.label}</span>
            {a.confidence != null && (
              <ConfidenceMeter value={a.confidence} signal={a.signal} />
            )}
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${signalColor(a.signal)}`}
            >
              {a.signal}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Segment({
  count,
  total,
  className,
  label,
}: {
  count: number;
  total: number;
  className: string;
  label: string;
}) {
  if (count === 0) return null;
  return (
    <span
      className={className}
      style={{ width: `${(count / total) * 100}%` }}
      title={`${label} ${count} 位`}
    />
  );
}

/** 置信度小量条。轨道用同色系浅色阶，让强弱在整条上都读得出来。 */
function ConfidenceMeter({ value, signal }: { value: number; signal?: string | null }) {
  const fill =
    signal === "看多"
      ? "bg-[var(--up)]"
      : signal === "看空"
        ? "bg-[var(--down)]"
        : "bg-[var(--flat)]";
  const track =
    signal === "看多"
      ? "bg-[var(--up-soft)]"
      : signal === "看空"
        ? "bg-[var(--down-soft)]"
        : "bg-[var(--surface-2)]";

  return (
    <span
      className="hidden items-center gap-1.5 sm:flex"
      title={`置信度 ${value}/100`}
    >
      <span className={`h-1 w-12 overflow-hidden rounded-full ${track}`}>
        <span
          className={`block h-full rounded-full ${fill}`}
          style={{ width: `${Math.max(3, value)}%` }}
        />
      </span>
      <span className="w-5 text-right text-[10px] text-[var(--muted)]">{value}</span>
    </span>
  );
}
