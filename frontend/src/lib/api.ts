// 后端 API 客户端。
// 所有请求都走相对路径 /api/*，由 next.config.ts 的 rewrites 代理到后端 ——
// 手机上只需要访问前端一个地址。

import type {
  AnalysisOptions,
  AnalysisRun,
  AnalysisRunSummary,
  Bar,
  BarPeriod,
  Depth,
  Diagnosis,
  FundInfo,
  GoldIntraday,
  GoldItem,
  Indicators,
  IndexQuote,
  Quote,
  SessionInfo,
  StockBasic,
  SymbolIndexStatus,
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
  quote: (symbol: string, signal?: AbortSignal) =>
    request<Quote>(`/market/quote/${encodeURIComponent(symbol)}`, { signal }),
  /** 批量行情：自选股列表和实时轮询用，20 只票 1 个请求而不是 20 个 */
  quotes: (symbols: string[], signal?: AbortSignal) =>
    request<Quote[]>(`/market/quotes?symbols=${encodeURIComponent(symbols.join(","))}`, {
      signal,
    }),
  bars: (symbol: string, days = 250, period: BarPeriod = "day", signal?: AbortSignal) =>
    request<Bar[]>(
      `/market/bars/${encodeURIComponent(symbol)}?days=${days}&period=${period}`,
      { signal },
    ),
  indicators: (symbol: string) =>
    request<Indicators>(`/market/indicators/${encodeURIComponent(symbol)}`),
  /** ETF 概况与重仓股。只对 ETF 有效，股票会返回 400 */
  fund: (symbol: string) => request<FundInfo>(`/market/fund/${encodeURIComponent(symbol)}`),
  indexes: (signal?: AbortSignal) => request<IndexQuote[]>("/market/indexes", { signal }),
  /** 当前交易时段。前端据此决定要不要轮询 —— 收盘后刷价格毫无意义 */
  session: (signal?: AbortSignal) => request<SessionInfo>("/market/session", { signal }),
  /** 数据源逐接口探测。会真的发网络请求，很慢，只在排查时手动调用 */
  diagnose: (signal?: AbortSignal) => request<Diagnosis>("/market/diagnose", { signal }),
  /** 本地代码目录状态：按名称搜索能不能用，看这个 */
  symbolsStatus: (signal?: AbortSignal) =>
    request<SymbolIndexStatus>("/market/symbols/status", { signal }),
  /** 重建代码目录。wait=true 会一直等到建完（可能要几十秒） */
  refreshSymbols: (wait = false, signal?: AbortSignal) =>
    request<SymbolIndexStatus & { started: boolean; waited: boolean; changed?: number }>(
      `/market/symbols/refresh?wait=${wait}`,
      { method: "POST", signal },
    ),

  // ---------- 金价 ----------
  /** 上金所 Au99.99 + 伦敦金。两个品种各自取、各自失败（看各自的 error） */
  gold: (signal?: AbortSignal) => request<GoldItem[]>("/gold", { signal }),
  goldBars: (code: string, period: "day" | "week", days: number, signal?: AbortSignal) =>
    request<Bar[]>(
      `/gold/${encodeURIComponent(code)}/bars?period=${period}&days=${days}`,
      { signal },
    ),
  /** 当前交易日的分时。只有上金所有，伦敦金会返回 404 */
  goldIntraday: (code: string, signal?: AbortSignal) =>
    request<GoldIntraday>(`/gold/${encodeURIComponent(code)}/intraday`, { signal }),

  // ---------- 分析 ----------
  /** 可选的分析深度与模型。说明文字按当前模型厂商生成 */
  analysisOptions: () => request<AnalysisOptions>("/analysis/options"),
  startAnalysis: (
    symbol: string,
    horizon: string,
    userNote = "",
    opts: { depth?: Depth; model?: string | null } = {},
  ) =>
    request<{ run_id: string; symbol: string; status: string; stream_url: string }>(
      "/analysis/runs",
      {
        method: "POST",
        body: JSON.stringify({
          symbol,
          horizon,
          user_note: userNote,
          depth: opts.depth ?? "standard",
          model: opts.model || null,
        }),
      },
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
