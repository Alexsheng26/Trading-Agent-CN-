"use client";

// 分析页：选股 -> 设周期 -> 跑流水线，实时看每个智能体的产出。

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AgentCard } from "@/components/AgentCard";
import { DecisionPanel } from "@/components/DecisionPanel";
import { FibPanel } from "@/components/FibPanel";
import { PriceChart } from "@/components/PriceChart";
import { StockSearch } from "@/components/StockSearch";
import { api } from "@/lib/api";
import { changeColor, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { useAnalysisStream } from "@/lib/useAnalysisStream";
import type { Bar, Horizon, Indicators, Quote } from "@/lib/types";

const HORIZONS: { value: Horizon; hint: string }[] = [
  { value: "短线", hint: "1-5 个交易日" },
  { value: "波段", hint: "2 周 - 2 个月" },
  { value: "中线", hint: "3-12 个月" },
];

export default function AnalyzePage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-[var(--muted)]">加载中…</div>}>
      <AnalyzeInner />
    </Suspense>
  );
}

function AnalyzeInner() {
  const router = useRouter();
  const params = useSearchParams();
  const symbol = params.get("symbol") ?? "";
  const runIdParam = params.get("run") ?? null;

  const [horizon, setHorizon] = useState<Horizon>("波段");
  const [note, setNote] = useState("");
  // 斐波那契默认叠加在图上 —— 用户点进个股就是想看关键价位
  const [showFib, setShowFib] = useState(true);
  const [runId, setRunId] = useState<string | null>(runIdParam);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [quote, setQuote] = useState<Quote | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [indicators, setIndicators] = useState<Indicators | null>(null);

  const stream = useAnalysisStream(runId);

  // 换标的时清掉上一次的结果
  useEffect(() => {
    setRunId(runIdParam);
  }, [runIdParam]);

  useEffect(() => {
    if (!symbol) {
      setQuote(null);
      setBars([]);
      setIndicators(null);
      return;
    }
    let alive = true;
    void Promise.all([
      api.quote(symbol).catch(() => null),
      api.bars(symbol, 250).catch(() => [] as Bar[]),
      api.indicators(symbol).catch(() => null),
    ]).then(([q, b, i]) => {
      if (!alive) return;
      setQuote(q);
      setBars(b);
      setIndicators(i);
    });
    return () => {
      alive = false;
    };
  }, [symbol]);

  const start = useCallback(async () => {
    if (!symbol) return;
    setStarting(true);
    setError(null);
    try {
      const { run_id } = await api.startAnalysis(symbol, horizon, note);
      setRunId(run_id);
      // 写进 URL：刷新页面/分享链接都能回到这次分析
      router.replace(`/analyze?symbol=${encodeURIComponent(symbol)}&run=${run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动分析失败");
    } finally {
      setStarting(false);
    }
  }, [symbol, horizon, note, router]);

  if (!symbol) {
    return (
      <div className="mx-auto w-full max-w-2xl px-4 py-6 md:px-6">
        <h1 className="mb-1 text-lg font-semibold">分析一只股票</h1>
        <p className="mb-4 text-sm text-[var(--muted)]">
          7 个智能体会分别从基本面、技术面、消息面、资金面分析，再经多空辩论和风控，最后给出决策。
        </p>
        <StockSearch
          autoFocus
          onPick={(s) => router.push(`/analyze?symbol=${encodeURIComponent(s.symbol)}`)}
        />
      </div>
    );
  }

  const stageOrder = stream.pipeline.length > 0 ? stream.pipeline : null;
  const running = runId !== null && !stream.finished;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-4 md:px-6 md:py-6">
      {/* ---------- 标的头部 ---------- */}
      <div className="mb-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-lg font-semibold">
            {quote?.name || stream.name || symbol}
          </h1>
          <span className="tabular text-sm text-[var(--muted)]">{symbol}</span>
        </div>
        {quote && (
          <div className="mt-1 flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className={`tabular text-2xl font-semibold ${changeColor(quote.change_pct)}`}>
              {fmtNum(quote.price)}
            </span>
            <span className={`tabular text-sm ${changeColor(quote.change_pct)}`}>
              {fmtPct(quote.change_pct)}
            </span>
            <span className="tabular text-xs text-[var(--muted)]">
              成交额 {fmtMoney(quote.amount)} · 换手 {fmtPct(quote.turnover_rate, false)} · PE{" "}
              {fmtNum(quote.pe_ttm)}
            </span>
          </div>
        )}
      </div>

      <PriceChart bars={bars} fib={indicators?.fib} showFib={showFib} />

      {indicators?.fib && (
        <div className="mt-3">
          <FibPanel
            fib={indicators.fib}
            close={quote?.price ?? indicators.close}
            enabled={showFib}
            onToggle={() => setShowFib((v) => !v)}
          />
        </div>
      )}

      {indicators?.patterns && indicators.patterns.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {indicators.patterns.map((p) => (
            <span
              key={p}
              className="rounded bg-[var(--surface-2)] px-2 py-1 text-[11px] text-[var(--muted)]"
            >
              {p}
            </span>
          ))}
        </div>
      )}

      {/* ---------- 启动区 ---------- */}
      {!runId && (
        <div className="mt-5 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="text-sm font-medium">分析周期</div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {HORIZONS.map((h) => (
              <button
                key={h.value}
                type="button"
                onClick={() => setHorizon(h.value)}
                className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
                  horizon === h.value
                    ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                    : "border-[var(--border)] bg-[var(--surface-2)]"
                }`}
              >
                <div className="text-sm font-medium">{h.value}</div>
                <div className="mt-0.5 text-[10px] text-[var(--muted)]">{h.hint}</div>
              </button>
            ))}
          </div>

          <label className="mt-4 block text-sm font-medium" htmlFor="note">
            想让智能体特别关注什么？<span className="font-normal text-[var(--muted)]">（可选）</span>
          </label>
          <textarea
            id="note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={500}
            rows={2}
            placeholder="例如：我已在 130 元附近持仓，想知道该不该止损"
            className="mt-2 w-full resize-none rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-sm outline-none placeholder:text-[var(--muted)] focus:border-[var(--accent)]"
          />

          <button
            type="button"
            onClick={() => void start()}
            disabled={starting}
            className="mt-4 w-full rounded-lg bg-[var(--accent)] px-4 py-3 text-sm font-medium text-white disabled:opacity-50"
          >
            {starting ? "启动中…" : "开始分析"}
          </button>
          <p className="mt-2 text-[11px] text-[var(--muted)]">
            整套流水线约需 1-3 分钟。中途可以锁屏或切走，回来后进度会自动接上。
          </p>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg bg-[var(--up-soft)] px-3 py-2 text-xs text-[var(--up)]">
          {error}
        </div>
      )}

      {/* ---------- 运行状态 ---------- */}
      {runId && (
        <div className="mt-5">
          <ConnectionBar state={stream} onRestart={() => setRunId(null)} />

          {stream.dataWarnings.length > 0 && (
            <div className="mt-3 rounded-lg bg-[var(--warn-soft)] px-3 py-2 text-[11px] leading-relaxed text-[var(--warn)]">
              <strong>部分数据未取到</strong>
              <ul className="mt-1 space-y-0.5">
                {stream.dataWarnings.map((w, i) => (
                  <li key={i}>· {w}</li>
                ))}
              </ul>
            </div>
          )}

          {stream.decision && (
            <div className="mt-4">
              <DecisionPanel decision={stream.decision} />
            </div>
          )}

          {stageOrder?.map((stage) => {
            const agents = stage.agents
              .map((a) => stream.agents[a.key])
              .filter((a): a is NonNullable<typeof a> => Boolean(a));
            if (agents.length === 0) return null;
            return (
              <section key={stage.stage} className="mt-5">
                <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-[var(--muted)]">
                  {stage.label}
                  {stream.currentStage === stage.stage && running && (
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
                  )}
                </h2>
                <div className="grid gap-3 lg:grid-cols-2">
                  {agents.map((agent) => (
                    <AgentCard key={agent.key} agent={agent} />
                  ))}
                </div>
              </section>
            );
          })}

          {Object.keys(stream.dataSources).length > 0 && (
            <p className="mt-6 text-[11px] leading-relaxed text-[var(--muted)]">
              数据来源：
              {Object.entries(stream.dataSources)
                .map(([k, v]) => `${k}=${v}`)
                .join("、")}
              {stream.elapsedSec != null && ` · 耗时 ${stream.elapsedSec}s`}
              {stream.usage?.input_tokens != null &&
                ` · token 输入 ${stream.usage.input_tokens} / 输出 ${stream.usage.output_tokens}`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ConnectionBar({
  state,
  onRestart,
}: {
  state: ReturnType<typeof useAnalysisStream>;
  onRestart: () => void;
}) {
  const label = {
    idle: "准备中",
    connecting: "正在连接…",
    live: "实时跟进中",
    polling: "连接中断，已切换为轮询（分析仍在服务端进行）",
    closed: state.error ? "分析失败" : "分析完成",
  }[state.connection];

  const tone =
    state.error || state.connection === "polling"
      ? "bg-[var(--warn-soft)] text-[var(--warn)]"
      : state.finished
        ? "bg-[var(--down-soft)] text-[var(--down)]"
        : "bg-[var(--accent-soft)] text-[var(--accent)]";

  return (
    <div className={`flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-xs ${tone}`}>
      <span>{state.error ? `${label}：${state.error}` : label}</span>
      {state.finished && (
        <button type="button" onClick={onRestart} className="shrink-0 underline">
          重新分析
        </button>
      )}
    </div>
  );
}
