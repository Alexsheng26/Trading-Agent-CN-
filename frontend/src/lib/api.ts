// 后端 API 客户端。
// 所有请求都走相对路径 /api/*，由 next.config.ts 的 rewrites 代理到后端 ——
// 手机上只需要访问前端一个地址。

import type {
  AnalysisRun,
  AnalysisRunSummary,
  Bar,
  Indicators,
  IndexQuote,
  Quote,
  StockBasic,
  SystemStatus,
  WatchlistItem,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    // 后端统一用 {detail: "..."} 返回错误
    let detail = `请求失败（HTTP ${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = body.detail[0]?.msg ?? detail;
    } catch {
      // 响应体不是 JSON，用默认文案
    }
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  // ---------- 系统 ----------
  status: () => request<SystemStatus>("/status"),

  // ---------- 行情 ----------
  search: (q: string, limit = 20, signal?: AbortSignal) =>
    request<StockBasic[]>(`/market/search?q=${encodeURIComponent(q)}&limit=${limit}`, {
      signal,
    }),
  quote: (symbol: string) => request<Quote>(`/market/quote/${encodeURIComponent(symbol)}`),
  bars: (symbol: string, days = 250) =>
    request<Bar[]>(`/market/bars/${encodeURIComponent(symbol)}?days=${days}`),
  indicators: (symbol: string) =>
    request<Indicators>(`/market/indicators/${encodeURIComponent(symbol)}`),
  indexes: () => request<IndexQuote[]>("/market/indexes"),

  // ---------- 分析 ----------
  startAnalysis: (symbol: string, horizon: string, userNote = "") =>
    request<{ run_id: string; symbol: string; status: string; stream_url: string }>(
      "/analysis/runs",
      { method: "POST", body: JSON.stringify({ symbol, horizon, user_note: userNote }) },
    ),
  getRun: (runId: string) => request<AnalysisRun>(`/analysis/runs/${runId}`),
  listRuns: (params: { symbol?: string; limit?: number; offset?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.symbol) search.set("symbol", params.symbol);
    search.set("limit", String(params.limit ?? 20));
    search.set("offset", String(params.offset ?? 0));
    return request<{
      items: AnalysisRunSummary[];
      total: number;
      limit: number;
      offset: number;
    }>(`/analysis/runs?${search}`);
  },
  cancelRun: (runId: string) =>
    request<{ run_id: string; status: string }>(`/analysis/runs/${runId}/cancel`, {
      method: "POST",
    }),
  deleteRun: (runId: string) => request<void>(`/analysis/runs/${runId}`, { method: "DELETE" }),

  // ---------- 自选股 ----------
  watchlist: (withQuotes = true) =>
    request<WatchlistItem[]>(`/watchlist?with_quotes=${withQuotes}`),
  addToWatchlist: (symbol: string, note = "", groupName = "默认") =>
    request<WatchlistItem>("/watchlist", {
      method: "POST",
      body: JSON.stringify({ symbol, note, group_name: groupName }),
    }),
  updateWatchlistItem: (
    id: number,
    patch: { group_name?: string; note?: string; sort_order?: number },
  ) =>
    request<WatchlistItem>(`/watchlist/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  removeFromWatchlist: (id: number) =>
    request<void>(`/watchlist/${id}`, { method: "DELETE" }),
};
