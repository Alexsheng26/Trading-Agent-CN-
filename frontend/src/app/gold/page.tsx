"use client";

// 金价页：上金所 Au99.99 / 伦敦金，当天分时 + 日K / 周K。
// 只看行情，不做 AI 分析 —— 想让 AI 分析黄金，分析场内黄金 ETF（页面底部有入口）。

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { IntradayChart } from "@/components/IntradayChart";
import { PriceChart } from "@/components/PriceChart";
import { StatRow, StatTile } from "@/components/StatTile";
import { api } from "@/lib/api";
import { changeColor, fmtNum, fmtPct, fmtSigned } from "@/lib/format";
import { useDocumentVisible, useLiveGold } from "@/lib/useLiveQuote";
import type { Bar, GoldIntraday, GoldPeriod } from "@/lib/types";

/** 能切换的品种。数据回来之前标题先用这里的名字 */
const INSTRUMENTS = [
  { code: "AU9999", label: "上金所 Au99.99" },
  { code: "XAU", label: "伦敦金" },
] as const;
const DEFAULT_CODE = "AU9999";

const PERIOD_KEY = "gold.period";
const PERIODS: { value: GoldPeriod; label: string; days: number }[] = [
  { value: "intraday", label: "分时", days: 0 },
  { value: "day", label: "日K", days: 250 },
  { value: "week", label: "周K", days: 1100 },
];
/** 分时一分钟刷一次（上金所官网本身就是延时行情） */
const INTRADAY_POLL_MS = 60_000;
/** 日K、周K 一天里基本不动 */
const BARS_CACHE_MS = 10 * 60_000;

function loadPeriod(): GoldPeriod | null {
  try {
    const v = window.localStorage.getItem(PERIOD_KEY);
    return v === "intraday" || v === "day" || v === "week" ? v : null;
  } catch {
    return null;
  }
}

function savePeriod(p: GoldPeriod): void {
  try {
    window.localStorage.setItem(PERIOD_KEY, p);
  } catch {
    // 写不进去就算了，下次用默认的分时
  }
}

/** "2026-10-26T10:00:00"（北京时间）-> "10-26 10:00" */
function wallClock(iso: string): string {
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
}

export default function GoldPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-[var(--muted)]">加载中…</div>}>
      <GoldInner />
    </Suspense>
  );
}

function GoldInner() {
  const router = useRouter();
  const params = useSearchParams();
  const raw = (params.get("code") ?? DEFAULT_CODE).toUpperCase();
  const instrument = INSTRUMENTS.find((i) => i.code === raw) ?? INSTRUMENTS[0];
  const code = instrument.code;

  const live = useLiveGold();
  const item = live.items.find((i) => i.code === code) ?? null;
  const quote = item?.quote ?? null;
  // 数据回来之前按品种表判断：只有上金所有分时
  const hasIntraday = item ? item.has_intraday : code === "AU9999";
  const decimals = item?.decimals ?? 2;

  // 记住上次看的周期。伦敦金没有分时，选着分时切过去时显示日K，切回来还是分时
  const [preferred, setPreferred] = useState<GoldPeriod>("intraday");
  useEffect(() => {
    const saved = loadPeriod();
    if (saved) setPreferred(saved);
  }, []);
  const period: GoldPeriod = preferred === "intraday" && !hasIntraday ? "day" : preferred;
  const pickPeriod = (p: GoldPeriod) => {
    setPreferred(p);
    savePeriod(p);
  };

  // ---------------- 分时 ----------------
  const visible = useDocumentVisible();
  const [intraday, setIntraday] = useState<GoldIntraday | null>(null);
  const [intradayError, setIntradayError] = useState<string | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);

  useEffect(() => {
    setIntraday(null);
    setIntradayError(null);
  }, [code]);

  useEffect(() => {
    if (period !== "intraday" || !hasIntraday || !visible) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();

    const load = async () => {
      setIntradayLoading(true);
      try {
        const data = await api.goldIntraday(code, controller.signal);
        if (!alive) return;
        setIntraday(data);
        setIntradayError(null);
      } catch (err) {
        if (!alive || controller.signal.aborted) return;
        // 手上有旧图就继续显示旧图，下一轮再试
        setIntradayError(err instanceof Error ? err.message : "取不到分时");
      } finally {
        if (alive) setIntradayLoading(false);
      }
      if (alive) timer = setTimeout(load, INTRADAY_POLL_MS);
    };
    void load();

    return () => {
      alive = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [code, period, hasIntraday, visible]);

  // ---------------- 日K / 周K ----------------
  const barsCache = useRef(new Map<string, { bars: Bar[]; at: number }>());
  const [bars, setBars] = useState<Bar[]>([]);
  const [barsError, setBarsError] = useState<string | null>(null);
  const [barsLoading, setBarsLoading] = useState(false);

  useEffect(() => {
    setBarsError(null);
    setBarsLoading(false);
    if (period === "intraday") return;
    const key = `${code}|${period}`;
    const cached = barsCache.current.get(key);
    // 有缓存先画缓存；没有就清空显示"加载中"，别让另一个品种的图顶着这个品种的标题
    setBars(cached?.bars ?? []);
    if (cached && Date.now() - cached.at < BARS_CACHE_MS) return;

    const controller = new AbortController();
    const days = PERIODS.find((p) => p.value === period)?.days ?? 250;
    setBarsLoading(true);
    api
      .goldBars(code, period, days, controller.signal)
      .then((b) => {
        barsCache.current.set(key, { bars: b, at: Date.now() });
        setBars(b);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (!cached) setBarsError(err instanceof Error ? err.message : "取不到 K 线");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBarsLoading(false);
      });
    return () => controller.abort();
  }, [code, period]);

  // ---------------- 展示 ----------------
  const color = changeColor(quote?.change_pct);
  const periodLabel = PERIODS.find((p) => p.value === period)?.label ?? "";
  const hint =
    period === "intraday"
      ? intraday
        ? `交易日 ${intraday.trading_day.slice(5)} · 夜盘 20:00 起算`
        : "当前交易日 · 每分钟一个价"
      : period === "day"
        ? code === "AU9999"
          ? "近 8 个月 · 最后一根是盘中由分时合成的"
          : "近 8 个月"
        : "近 3 年";
  const loadingNote =
    (period === "intraday" ? intradayLoading && intraday !== null : barsLoading && bars.length > 0)
      ? " · 更新中…"
      : "";

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-4 md:px-6 md:py-6">
      {/* ---------- 品种切换 ---------- */}
      <div
        role="tablist"
        aria-label="黄金品种"
        className="mb-4 inline-flex rounded-lg border border-[var(--border)] bg-[var(--surface)] p-0.5"
      >
        {INSTRUMENTS.map((i) => (
          <button
            key={i.code}
            type="button"
            role="tab"
            aria-selected={code === i.code}
            onClick={() => router.replace(`/gold?code=${i.code}`)}
            className={`rounded-md px-3 py-1.5 text-xs transition-colors ${
              code === i.code
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--muted)] hover:text-[var(--text)]"
            }`}
          >
            {i.label}
          </button>
        ))}
      </div>

      {/* ---------- 价格头部 ---------- */}
      <div className="mb-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-lg font-semibold">{item?.name ?? instrument.label}</h1>
          {item && <span className="text-sm text-[var(--muted)]">{item.unit}</span>}
          {/* 分时取不到时价格是上一根日线的收盘价，必须标出来 */}
          {quote && !quote.is_realtime && (
            <span className="rounded bg-[var(--warn-soft)] px-1.5 py-0.5 text-[10px] text-[var(--warn)]">
              收盘价{quote.quote_date ? ` · ${quote.quote_date.slice(5)}` : ""}（非实时）
            </span>
          )}
        </div>
        <div className="mt-2 flex flex-wrap items-end gap-x-4 gap-y-1">
          <span className={`text-3xl font-semibold leading-none ${quote ? color : "text-[var(--muted)]"}`}>
            {quote ? fmtNum(quote.price, decimals) : "—"}
          </span>
          {quote && (
            <span className={`tabular text-sm ${color}`}>
              {fmtSigned(quote.change, decimals)}
              <span className="ml-2">{fmtPct(quote.change_pct)}</span>
            </span>
          )}
        </div>
        {item && (
          <div className="mt-1.5 text-[11px] text-[var(--muted)]">
            {quote?.as_of ? `数据时间 ${wallClock(quote.as_of)} · ` : ""}
            {item.source} · 每分钟刷新
          </div>
        )}
      </div>

      {/* 取不到要说清原因：一个"—"却不给理由，用户只会以为工具坏了 */}
      {item?.error && (
        <div className="mb-4 rounded-lg bg-[var(--warn-soft)] px-3 py-2 text-[11px] leading-relaxed text-[var(--warn)]">
          <strong>{item.name}取不到</strong>：{item.error}
          <br />
          去「设置 → 数据源诊断」看「金价」开头的那几行是哪一步失败。
        </div>
      )}
      {!item && live.error && (
        <div className="mb-4 rounded-lg bg-[var(--warn-soft)] px-3 py-2 text-[11px] leading-relaxed text-[var(--warn)]">
          <strong>金价取不到</strong>：{live.error}
        </div>
      )}

      {quote && (
        <div className="mb-4">
          <StatRow>
            <StatTile label="今开" value={fmtNum(quote.open, decimals)} />
            <StatTile label="最高" value={fmtNum(quote.high, decimals)} />
            <StatTile label="最低" value={fmtNum(quote.low, decimals)} />
            <StatTile label="昨收" value={fmtNum(quote.prev_close, decimals)} />
          </StatRow>
        </div>
      )}

      {/* ---------- 分时 / 日K / 周K ---------- */}
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="min-w-0 text-[11px] text-[var(--muted)]">
          {hint}
          {loadingNote}
        </span>
        <div
          role="tablist"
          aria-label="走势周期"
          className="inline-flex shrink-0 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-0.5"
        >
          {PERIODS.filter((p) => p.value !== "intraday" || hasIntraday).map((p) => (
            <button
              key={p.value}
              type="button"
              role="tab"
              aria-selected={period === p.value}
              onClick={() => pickPeriod(p.value)}
              className={`rounded-md px-3 py-1 text-xs transition-colors ${
                period === p.value
                  ? "bg-[var(--accent)] text-white"
                  : "text-[var(--muted)] hover:text-[var(--text)]"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {period === "intraday" ? (
        <IntradayChart
          points={intraday?.points ?? []}
          prevClose={intraday?.prev_close ?? quote?.prev_close}
          decimals={decimals}
          emptyText={
            intradayError ? `分时取不到：${intradayError}` : intraday ? "暂无分时数据" : "加载中…"
          }
        />
      ) : (
        <PriceChart
          bars={bars}
          decimals={decimals}
          emptyText={
            barsError
              ? `${periodLabel}取不到：${barsError}`
              : barsLoading
                ? "加载中…"
                : "暂无 K 线数据"
          }
        />
      )}

      {/* ---------- 说明 ---------- */}
      <section className="mt-5 space-y-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 text-xs leading-relaxed text-[var(--muted)]">
        {code === "AU9999" ? (
          <>
            <p>
              <strong className="text-[var(--text)]">上金所 Au99.99</strong>{" "}
              是国内金价的基准：银行的纸黄金、积存金，还有场内黄金 ETF，报价基本都跟着它走，
              再加上各自的买卖点差和费用。
            </p>
            <p>
              上金所有夜盘（20:00 到次日 02:30），晚上开始的夜盘算下一个交易日 ——
              所以晚上看到的涨跌，是相对当天下午的收盘价。上金所官网给的本身是延时行情。
            </p>
          </>
        ) : (
          <>
            <p>
              <strong className="text-[var(--text)]">伦敦金</strong>{" "}
              是国际现货金价，按美元/盎司报价，工作日几乎 24 小时都在交易。
              换算成人民币每克，大约是「伦敦金 × 美元汇率 ÷ 31.1」（1 盎司约 31.1 克）。
            </p>
            <p>
              免费数据源里找不到可靠的伦敦金分时，这里只有日K和周K；想看当天的走势，
              切到上金所 Au99.99 的分时。
            </p>
          </>
        )}
        <p className="border-t border-[var(--border)] pt-2.5">
          金价只看行情，不做 AI 分析。想让 AI 分析黄金，可以分析场内黄金 ETF（比如
          518880），它的价格跟着上金所金价走。{" "}
          <Link href="/analyze?symbol=518880" className="text-[var(--accent)] hover:underline">
            去分析 518880 →
          </Link>
        </p>
      </section>
    </div>
  );
}
