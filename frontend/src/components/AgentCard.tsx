"use client";

import { useState } from "react";
import { signalColor } from "@/lib/format";
import type { LiveAgent } from "@/lib/useAnalysisStream";
import { Markdown } from "./Markdown";

export function AgentCard({ agent }: { agent: LiveAgent }) {
  const [showThinking, setShowThinking] = useState(false);
  const streaming = agent.status === "streaming";

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <StatusDot status={agent.status} />
          <span className="truncate text-sm font-medium">{agent.label}</span>
        </div>
        {agent.signal && (
          <span
            className={`shrink-0 rounded px-2 py-0.5 text-[11px] font-medium ${signalColor(agent.signal)}`}
          >
            {agent.signal}
            {agent.confidence != null && ` ${agent.confidence}`}
          </span>
        )}
      </div>

      {agent.status === "pending" && (
        <p className="mt-3 text-xs text-[var(--muted)]">等待中…</p>
      )}

      {agent.error && (
        <p className="mt-3 rounded-lg bg-[var(--down-soft)] px-3 py-2 text-xs text-[var(--down)]">
          该智能体执行失败：{agent.error}
          <br />
          <span className="text-[var(--muted)]">其余分析不受影响，最终决策会标注此缺失。</span>
        </p>
      )}

      {agent.thinking && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setShowThinking((v) => !v)}
            className="text-[11px] text-[var(--muted)] underline decoration-dotted"
          >
            {showThinking ? "收起思考过程" : "查看思考过程"}
          </button>
          {showThinking && (
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--surface-2)] p-3 text-[11px] leading-relaxed text-[var(--muted)]">
              {agent.thinking}
            </pre>
          )}
        </div>
      )}

      {agent.content && (
        <div className={`mt-2 ${streaming ? "streaming-cursor" : ""}`}>
          <Markdown>{agent.content}</Markdown>
        </div>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: LiveAgent["status"] }) {
  const cls = {
    pending: "bg-[var(--border)]",
    streaming: "bg-[var(--accent)] animate-pulse",
    done: "bg-[var(--down)]",
    error: "bg-[var(--up)]",
  }[status];
  return <span className={`h-2 w-2 shrink-0 rounded-full ${cls}`} />;
}
