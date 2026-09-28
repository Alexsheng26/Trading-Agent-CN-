"use client";

// 流水线进度：4 个阶段 + 每阶段的智能体完成数。
//
// 为什么值得单独做一个组件：一次分析要跑 1-3 分钟，用户盯着屏幕时最想知道
// "还有多久"。原来只在阶段标题旁放了个闪烁圆点，看不出整体进度。

import type { PipelineStage, Stage } from "@/lib/types";
import type { LiveAgent } from "@/lib/useAnalysisStream";

export function StageStepper({
  pipeline,
  agents,
  currentStage,
  finished,
}: {
  pipeline: PipelineStage[];
  agents: Record<string, LiveAgent>;
  currentStage: Stage | null;
  finished: boolean;
}) {
  if (pipeline.length === 0) return null;

  const stageState = (stage: PipelineStage) => {
    const keys = stage.agents.map((a) => a.key);
    const done = keys.filter((k) => {
      const st = agents[k]?.status;
      return st === "done" || st === "error";
    }).length;
    return { done, total: keys.length };
  };

  return (
    <ol className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 md:mx-0 md:grid md:grid-cols-4 md:px-0">
      {pipeline.map((stage, i) => {
        const { done, total } = stageState(stage);
        const complete = done === total && total > 0;
        const active = currentStage === stage.stage && !finished;
        const pct = total > 0 ? (done / total) * 100 : 0;

        return (
          <li
            key={stage.stage}
            className={`min-w-[8.5rem] shrink-0 rounded-xl border px-3 py-2.5 transition-colors md:min-w-0 ${
              active
                ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                : complete
                  ? "border-[var(--border)] bg-[var(--surface)]"
                  : "border-dashed border-[var(--border)] bg-transparent"
            }`}
          >
            <div className="flex items-center gap-1.5">
              <StageIcon index={i} active={active} complete={complete} />
              <span
                className={`truncate text-xs font-medium ${
                  active
                    ? "text-[var(--accent)]"
                    : complete
                      ? "text-[var(--text)]"
                      : "text-[var(--muted)]"
                }`}
              >
                {stage.label}
              </span>
            </div>

            {/* 进度条：未填充轨道用同色系浅色阶，不用中性灰 —— 
                这样"进行到哪了"在整条上都读得出来 */}
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--accent-soft)]">
              <div
                className="h-full rounded-full bg-[var(--accent)] transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="mt-1 text-[10px] text-[var(--muted)]">
              {done}/{total}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function StageIcon({
  index,
  active,
  complete,
}: {
  index: number;
  active: boolean;
  complete: boolean;
}) {
  if (complete) {
    return (
      <svg
        viewBox="0 0 16 16"
        className="h-3.5 w-3.5 shrink-0 text-[var(--down)]"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M3 8.5l3.5 3.5L13 4.5" />
      </svg>
    );
  }
  if (active) {
    return (
      <span className="h-3.5 w-3.5 shrink-0 animate-pulse rounded-full bg-[var(--accent)]" />
    );
  }
  return (
    <span className="grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border border-[var(--border)] text-[8px] text-[var(--muted)]">
      {index + 1}
    </span>
  );
}
