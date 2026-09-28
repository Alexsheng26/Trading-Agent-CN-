"use client";

// 实时行情轮询。
//
// 三条原则，都是为了别把免费数据源打挂、别把手机电量耗光：
//   1. **只在交易时段轮询**。收盘后价格不会变，每 5 秒拉一次纯属浪费。
//      节奏由后端 /market/session 给出，前端不自己写死交易时间。
//   2. **切到后台就停**。手机上 PWA 切走后继续轮询毫无意义，回到前台
//      立刻补一次，保证看到的价格是新的。
//   3. **失败退避，且不清空旧数据**。数据源抖一下就把界面清白屏是最糟的
//      体验 —— 保留上次的价格，只标注"数据可能已过期"。

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { IndexQuote, Quote, SessionInfo } from "./types";

/** 时段信息拿不到时的保守轮询间隔：功能不能因为一个辅助接口挂了就停摆 */
const FALLBACK_POLL_MS = 30_000;
/** 连续失败的退避上限 */
const MAX_BACKOFF_MS = 60_000;
/** 时段复查间隔的上下限。next_change 很远时没必要频繁复查 */
const RECHECK_MIN_MS = 5_000;
const RECHECK_MAX_MS = 15 * 60_000;

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

// ---------------------------------------------------------------- 页面可见性

/** 页面是否在前台。切到后台时暂停轮询，回来时立刻刷新。 */
export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const sync = () => setVisible(!document.hidden);
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  return visible;
}

/**
 * 定时重渲染，给"距开盘 2 小时 13 分"这类倒计时走秒用。
 * 只在 enabled 时计时，切到后台或不需要倒计时就停。
 */
export function useTicker(intervalMs: number, enabled = true): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);

  return now;
}

// ---------------------------------------------------------------- 交易时段

export interface TradingSession {
  session: SessionInfo | null;
  /** 服务端时间 - 浏览器时间。用户电脑时钟不准时，倒计时靠它校正 */
  skewMs: number;
  /** 建议的轮询间隔（毫秒）。null = 不必轮询 */
  pollMs: number | null;
}

function recheckDelay(s: SessionInfo, serverNowMs: number): number {
  if (!s.next_change) return RECHECK_MAX_MS;
  const until = Date.parse(s.next_change) - serverNowMs;
  if (Number.isNaN(until)) return RECHECK_MAX_MS;
  // 多等 2 秒，确保复查时已经跨过了边界
  return Math.min(Math.max(until + 2_000, RECHECK_MIN_MS), RECHECK_MAX_MS);
}

/**
 * 跟踪当前交易时段。
 * 全站共用同一份节奏，避免每个组件各自写死刷新间隔。
 */
export function useTradingSession(): TradingSession {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [skewMs, setSkewMs] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();

    const tick = async () => {
      try {
        const s = await api.session(controller.signal);
        if (!alive) return;
        setSession(s);
        const serverNow = Date.parse(s.as_of);
        const ok = !Number.isNaN(serverNow);
        if (ok) setSkewMs(serverNow - Date.now());
        timer = setTimeout(tick, recheckDelay(s, ok ? serverNow : Date.now()));
      } catch (err) {
        if (!alive || isAbort(err)) return;
        // 时段接口挂了不影响看价格，稍后重试
        timer = setTimeout(tick, 60_000);
      }
    };
    void tick();

    return () => {
      alive = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, []);

  const pollMs =
    session === null
      ? FALLBACK_POLL_MS
      : session.is_live
        ? Math.max(session.poll_interval ?? 5, 3) * 1000
        : null;

  return { session, skewMs, pollMs };
}

// ---------------------------------------------------------------- 通用轮询

interface PolledState<T> {
  data: T | null;
  /** 上次成功取到数据的本地时刻（epoch ms） */
  updatedAt: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/**
 * 带退避与去重的轮询。
 *
 * `key` 是身份标识（标的代码等）：变了就丢弃旧数据重新开始。
 * `fetcher` 允许每次渲染都是新函数 —— 存在 ref 里，不作为依赖，
 * 否则每次渲染都会重启轮询。
 */
function usePolled<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  pollMs: number | null,
  enabled: boolean,
  onData?: (next: T, prev: T | null) => void,
): PolledState<T> {
  const [data, setData] = useState<T | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const onDataRef = useRef(onData);
  onDataRef.current = onData;

  const inFlight = useRef<AbortController | null>(null);
  const failures = useRef(0);
  //: 上次成功取数的时刻。从后台切回来时靠它判断数据有多旧。
  const lastUpdate = useRef<number | null>(null);
  // 上一次的数据存在 ref 里：onData 必须在 state updater 之外调用，
  // React 的 updater 可能被重复执行（StrictMode 下必然），副作用会翻倍。
  const prevData = useRef<T | null>(null);

  const fetchOnce = useCallback(async () => {
    if (!key) return;
    // 上一次还没回来就跳过：慢网络下别把请求堆起来
    if (inFlight.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    setLoading(true);
    try {
      const next = await fetcherRef.current(controller.signal);
      if (controller.signal.aborted) return;
      failures.current = 0;
      onDataRef.current?.(next, prevData.current);
      prevData.current = next;
      setData(next);
      lastUpdate.current = Date.now();
      setUpdatedAt(lastUpdate.current);
      setError(null);
    } catch (err) {
      if (isAbort(err)) return;
      failures.current += 1;
      setError(err instanceof Error ? err.message : "行情获取失败");
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
      setLoading(false);
    }
  }, [key]);

  // 换标的：清空旧数据并立刻取一次（不管在不在交易时段 —— 收盘也要看到收盘价）
  useEffect(() => {
    failures.current = 0;
    prevData.current = null;
    lastUpdate.current = null;
    setData(null);
    setUpdatedAt(null);
    setError(null);
    if (!key) return;
    void fetchOnce();
    return () => {
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [key, fetchOnce]);

  // 轮询循环。先等再取，所以不会和上面的首次请求撞在一起。
  useEffect(() => {
    if (!key || !enabled || pollMs === null) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = (delay: number) => {
      timer = setTimeout(async () => {
        if (!alive) return;
        await fetchOnce();
        if (alive) schedule(nextDelay());
      }, delay);
    };

    const nextDelay = () =>
      // 连续失败时指数退避，避免对着一个已经挂掉的数据源猛打
      failures.current > 0
        ? Math.min(pollMs * 2 ** failures.current, MAX_BACKOFF_MS)
        : pollMs;

    // 首次等待要扣掉"数据已经放了多久"。
    // 从后台切回来、或刚进入交易时段时，手里的价格往往已经过期了 ——
    // 再干等一个完整间隔，用户看到的就是旧价。
    const age = lastUpdate.current === null ? Infinity : Date.now() - lastUpdate.current;
    schedule(Math.max(nextDelay() - age, 0));

    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [key, enabled, pollMs, fetchOnce]);

  const refresh = useCallback(() => {
    failures.current = 0;  // 手动刷新清掉退避
    void fetchOnce();
  }, [fetchOnce]);

  return { data, updatedAt, loading, error, refresh };
}

// ---------------------------------------------------------------- 单只行情

export interface LiveQuote {
  quote: Quote | null;
  session: SessionInfo | null;
  /** 是否正在轮询（交易时段 + 页面在前台） */
  polling: boolean;
  updatedAt: number | null;
  loading: boolean;
  error: string | null;
  /** 距下次时段变化的毫秒数，已用服务端时间校正 */
  msToNextChange: number | null;
  /** 最近一次价格变动：dir 为方向，seq 递增用于触发闪烁动画 */
  tick: { dir: 1 | 0 | -1; seq: number };
  refresh: () => void;
}

/** 单只股票的实时行情。分析页头部用。 */
export function useLiveQuote(symbol: string): LiveQuote {
  const { session, skewMs, pollMs } = useTradingSession();
  const visible = useDocumentVisible();
  const [tick, setTick] = useState<{ dir: 1 | 0 | -1; seq: number }>({ dir: 0, seq: 0 });

  const onData = useCallback((next: Quote, prev: Quote | null) => {
    if (!prev || prev.symbol !== next.symbol || prev.price === next.price) return;
    setTick((t) => ({ dir: next.price > prev.price ? 1 : -1, seq: t.seq + 1 }));
  }, []);

  const fetcher = useCallback(
    (signal: AbortSignal) => api.quote(symbol, signal),
    [symbol],
  );

  const { data, updatedAt, loading, error, refresh } = usePolled(
    symbol,
    fetcher,
    pollMs,
    visible,
    onData,
  );

  const msToNextChange =
    session?.next_change != null
      ? Date.parse(session.next_change) - (Date.now() + skewMs)
      : null;

  return {
    quote: data,
    session,
    polling: visible && pollMs !== null,
    updatedAt,
    loading,
    error,
    msToNextChange: msToNextChange != null && !Number.isNaN(msToNextChange) ? msToNextChange : null,
    tick,
    refresh,
  };
}

// ---------------------------------------------------------------- 批量行情

export interface LiveQuotes {
  quotes: Record<string, Quote>;
  session: SessionInfo | null;
  polling: boolean;
  updatedAt: number | null;
  error: string | null;
  refresh: () => void;
}

/**
 * 一组股票的实时行情（自选股列表用）。
 * 走批量接口：20 只票每轮 1 个请求，而不是 20 个。
 */
export function useLiveQuotes(symbols: string[]): LiveQuotes {
  const { session, pollMs } = useTradingSession();
  const visible = useDocumentVisible();

  // 排序后拼成 key：列表顺序变化不该重启轮询
  const key = [...symbols].sort().join(",");

  const fetcher = useCallback(
    async (signal: AbortSignal) => {
      if (!key) return {} as Record<string, Quote>;
      const list = await api.quotes(key.split(","), signal);
      return Object.fromEntries(list.map((q) => [q.symbol, q]));
    },
    [key],
  );

  const { data, updatedAt, error, refresh } = usePolled(
    key,
    fetcher,
    pollMs,
    visible,
  );

  return {
    quotes: data ?? {},
    session,
    polling: visible && pollMs !== null && key !== "",
    updatedAt,
    error,
    refresh,
  };
}

// ---------------------------------------------------------------- 大盘指数

export interface LiveIndexes {
  indexes: IndexQuote[];
  session: SessionInfo | null;
  polling: boolean;
  updatedAt: number | null;
  refresh: () => void;
}

/** 大盘指数，跟着交易时段一起刷。 */
export function useLiveIndexes(): LiveIndexes {
  const { session, pollMs } = useTradingSession();
  const visible = useDocumentVisible();

  const fetcher = useCallback((signal: AbortSignal) => api.indexes(signal), []);

  // 指数比个股慢一档：大盘看趋势，不需要 5 秒一跳
  const slowed = pollMs === null ? null : Math.max(pollMs, 10_000);
  const { data, updatedAt, refresh } = usePolled("indexes", fetcher, slowed, visible);

  return {
    indexes: data ?? [],
    session,
    polling: visible && slowed !== null,
    updatedAt,
    refresh,
  };
}
