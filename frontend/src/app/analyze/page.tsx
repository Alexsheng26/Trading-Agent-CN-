"use client";

// 分析页：选股 -> 设周期 -> 跑流水线，实时看每个智能体的产出。

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AgentCard } from "@/components/AgentCard";
import { DecisionPanel } from "@/components/DecisionPanel";
import { DepthPicker, RunConfigLine } from "@/components/DepthPicker";
import { EtfPanel } from "@/components/EtfPanel";
import { FibPanel } from "@/components/FibPanel";
import { LivePrice } from "@/components/LivePrice";
import { PriceChart } from "@/components/PriceChart";
import { SignalStrip } from "@/components/SignalStrip";
import { StageStepper } from "@/components/StageStepper";
import { StatRow, StatTile } from "@/components/StatTile";
import { StockSearch } from "@/components/StockSearch";
import { api } from "@/lib/api";
import { fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { isEtfSymbol, priceDecimals } from "@/lib/symbols";
import { useAnalysisStream } from "@/lib/useAnalysisStream";
import { useLiveQuote } from "@/lib/useLiveQuote";
import type {
  AnalysisOptions,
  Bar,
  Depth,
  FundInfo,
  Horizon,
  Indicators,
} from "@/lib/types";

// 记住上次选的档位（只是本机的便利，读写失败就用默认值）
const DEPTH_KEY = "analyze.depth";

function loadDepth(): Depth | null {
  try {
    const v = window.localStorage.getItem(DEPTH_KEY);
    return v === "quick" || v === "standard" || v === "deep" ? v : null;
  } catch {
    return null;
  }
}

function saveDepth(d: Depth): void {
  try {
    window.localStorage.setItem(DEPTH_KEY, d);
  } catch {
    // 隐私模式等场景写不进去，无所谓
  }
}

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
  const [depth, setDepth] = useState<Depth>("standard");
  // "" = 按档位自动选模型
  const [model, setModel] = useState("");
  const [options, setOptions] = useState<AnalysisOptions | null>(null);
  // 斐波那契默认叠加在图上 —— 用户点进个股就是想看关键价位
  const [showFib, setShowFib] = useState(true);
  const [runId, setRunId] = useState<string | null>(runIdParam);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [bars, setBars] = useState<Bar[]>([]);
  const [indicators, setIndicators] = useState<Indicators | null>(null);
  // ETF 概况与重仓股（只有 ETF 才拉）
  const [fund, setFund] = useState<FundInfo | null>(null);
  const [fundLoading, setFundLoading] = useState(false);
  const [fundError, setFundError] = useState<string | null>(null);
  const isEtf = isEtfSymbol(symbol);

  // 价格单独走实时轮询；K线和指标是日线级别的，没必要跟着刷
  const live = useLiveQuote(symbol);
  const quote = live.quote;
  const stream = useAnalysisStream(runId);

  useEffect(() => {
    const saved = loadDepth();
    if (saved) setDepth(saved);
    let alive = true;
    api
      .analysisOptions()
      .then((o) => {
        if (!alive) return;
        setOptions(o);
        // 后端的可选模型变了（改过 .env），之前选的不在列表里就回到自动
        setModel((m) => (m && !o.models.some((x) => x.value === m) ? "" : m));
      })
      .catch(() => undefined); // 拿不到就用兜底的三档按钮，照样能分析
    return () => {
      alive = false;
    };
  }, []);

  const pickDepth = useCallback((d: Depth) => {
    setDepth(d);
    saveDepth(d);
  }, []);

  // 换标的时清掉上一次的结果
  useEffect(() => {
    setRunId(runIdParam);
  }, [runIdParam]);

  useEffect(() => {
    if (!symbol) {
      setBars([]);
      setIndicators(null);
      return;
    }
    let alive = true;
    void Promise.all([
      api.bars(symbol, 250).catch(() => [] as Bar[]),
      api.indicators(symbol).catch(() => null),
    ]).then(([b, i]) => {
      if (!alive) return;
      setBars(b);
      setIndicators(i);
    });
    return () => {
      alive = false;
    };
  }, [symbol]);

  useEffect(() => {
    setFund(null);
    setFundError(null);
    if (!symbol || !isEtf) return;
    let alive = true;
    setFundLoading(true);
    api
      .fund(symbol)
      .then((f) => alive && setFund(f))
      .catch((e) => alive && setFundError(e instanceof Error ? e.message : "取不到 ETF 概况"))
      .finally(() => alive && setFundLoading(false));
    return () => {
      alive = false;
    };
  }, [symbol, isEtf]);

  const start = useCallback(async () => {
    if (!symbol) return;
    setStarting(true);
    setError(null);
    try {
      const { run_id } = await api.startAnalysis(symbol, horizon, note, { depth, model });
      setRunId(run_id);
      // 写进 URL：刷新页面/分享链接都能回到这次分析
      router.replace(`/analyze?symbol=${encodeURIComponent(symbol)}&run=${run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动分析失败");
    } finally {
      setStarting(false);
    }
  }, [symbol, horizon, note, depth, model, router]);

  if (!symbol) {
    return (
      <div className="mx-auto w-full max-w-2xl px-4 py-6 md:px-6">
        <h1 className="mb-1 text-lg font-semibold">分析一只股票或 ETF</h1>
        <p className="mb-4 text-sm text-[var(--muted)]">
          7 个智能体会分别从基本面、技术面、消息面、资金面分析，再经多空辩论和风控，最后给出决策。
          ETF 的基本面看的是跟踪指数和重仓股。
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
  // ETF 三位小数（最小变动 0.001 元）
  const digits = priceDecimals(symbol, quote);

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-4 md:px-6 md:py-6">
      {/* ---------- 标的头部（实时价格） ---------- */}
      <LivePrice symbol={symbol} fallbackName={stream.name} live={live}>
        {indicators?.fib && (
          <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">
            {indicators.fib.direction}段 · 已回撤 {fmtNum(indicators.fib.retracement_pct, 0)}%
          </span>
        )}
      </LivePrice>

      {/* ---------- 关键指标统计块 ---------- */}
      {(quote || indicators) && (
        <div className="mb-4">
          <StatRow>
            <StatTile label="成交额" value={fmtMoney(quote?.amount)} />
            <StatTile label="换手率" value={fmtPct(quote?.turnover_rate, false)} />
            {isEtf ? (
              // ETF 没有市盈率；规模是它最常被问到的一个数（太小有清盘风险）
              <StatTile label="规模" value={fmtMoney(fund?.profile?.asset_size)} />
            ) : (
              <StatTile label="市盈率 TTM" value={fmtNum(quote?.pe_ttm)} />
            )}
            <StatTile
              label="20 日涨幅"
              value={fmtPct(indicators?.return_20d)}
              direction={indicators?.return_20d ?? null}
            />
            <StatTile
              label="年化波动"
              value={fmtPct(indicators?.volatility_20d, false)}
              hint="20 日"
            />
            <StatTile
              label="ATR 占比"
              value={fmtPct(indicators?.atr_pct, false)}
              hint="日均波幅"
            />
          </StatRow>
        </div>
      )}

      <PriceChart bars={bars} fib={indicators?.fib} showFib={showFib} decimals={digits} />

      {indicators?.fib && (
        <div className="mt-3">
          <FibPanel
            fib={indicators.fib}
            close={quote?.price ?? indicators.close}
            enabled={showFib}
            onToggle={() => setShowFib((v) => !v)}
            decimals={digits}
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

      {isEtf && (
        <div className="mt-3">
          <EtfPanel info={fund} loading={fundLoading} error={fundError} />
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

          <div className="mt-4">
            <DepthPicker
              options={options}
              depth={depth}
              onDepth={pickDepth}
              model={model}
              onModel={setModel}
            />
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
            标准档约需 1-3 分钟，快速档更短、深度档明显更长。中途可以锁屏或切走，回来后进度会自动接上。
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
          <RunConfigLine config={stream.config} />

          {stream.pipeline.length > 0 && (
            <div className="mt-3">
              <StageStepper
                pipeline={stream.pipeline}
                agents={stream.agents}
                currentStage={stream.currentStage}
                finished={stream.finished}
              />
            </div>
          )}

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

          {/* 倾向一览排在各智能体正文之前：先看整体分歧，再看谁怎么说 */}
          <div className="mt-4">
            <SignalStrip agents={stream.agents} />
          </div>

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
