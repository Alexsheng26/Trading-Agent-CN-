// 与后端 app/schemas 对应的类型定义。

export type Horizon = "短线" | "波段" | "中线";
export type Signal = "看多" | "中性" | "看空";
export type Action = "买入" | "加仓" | "持有" | "减仓" | "卖出" | "观望";
export type Stage = "analysts" | "debate" | "risk" | "decision";

export interface StockBasic {
  symbol: string;
  name: string;
  exchange: string;
  board?: string;
  industry?: string;
  list_date?: string | null;
}

export interface Quote {
  symbol: string;
  name: string;
  price: number;
  prev_close?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  change_pct?: number | null;
  amount?: number | null;
  turnover_rate?: number | null;
  pe_ttm?: number | null;
  pb?: number | null;
  total_market_cap?: number | null;
  float_market_cap?: number | null;
  updated_at?: string | null;
}

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  change_pct?: number | null;
}

export interface Indicators {
  close?: number | null;
  ma5?: number | null;
  ma10?: number | null;
  ma20?: number | null;
  ma60?: number | null;
  ma120?: number | null;
  macd_dif?: number | null;
  macd_dea?: number | null;
  macd_hist?: number | null;
  rsi6?: number | null;
  rsi14?: number | null;
  boll_upper?: number | null;
  boll_mid?: number | null;
  boll_lower?: number | null;
  kdj_k?: number | null;
  kdj_d?: number | null;
  kdj_j?: number | null;
  atr14?: number | null;
  atr_pct?: number | null;
  high_60?: number | null;
  low_60?: number | null;
  pct_from_high_60?: number | null;
  return_20d?: number | null;
  return_60d?: number | null;
  volatility_20d?: number | null;
  volume_ratio_5_20?: number | null;
  patterns: string[];
}

export interface IndexQuote {
  code: string;
  name: string;
  price: number;
  change_pct?: number | null;
}

export interface AgentOutput {
  key: string;
  label: string;
  stage: Stage;
  content: string;
  thinking: string;
  signal?: Signal | null;
  confidence?: number | null;
  error: string;
  usage?: Record<string, number> | null;
}

export interface Decision {
  action: Action;
  conviction: number;
  position_pct: number;
  horizon: string;
  entry_zone: string;
  stop_loss: string;
  take_profit: string;
  summary: string;
  key_reasons: string[];
  key_risks: string[];
  dissent: string;
  data_quality: string;
  disclaimer: string;
}

export interface AnalysisRun {
  id: string;
  symbol: string;
  name: string;
  horizon: string;
  user_note: string;
  status: "pending" | "running" | "done" | "error" | "cancelled";
  error: string;
  decision?: Decision | null;
  outputs: AgentOutput[];
  data_sources?: Record<string, string> | null;
  data_warnings?: string[] | null;
  usage?: Record<string, number> | null;
  llm_provider: string;
  elapsed_sec?: number | null;
  created_at?: string | null;
  finished_at?: string | null;
}

export interface AnalysisRunSummary {
  id: string;
  symbol: string;
  name: string;
  horizon: string;
  status: string;
  action?: string | null;
  conviction?: number | null;
  created_at?: string | null;
  elapsed_sec?: number | null;
}

export interface WatchlistItem {
  id: number;
  symbol: string;
  name: string;
  group_name: string;
  note: string;
  sort_order: number;
  created_at?: string | null;
  quote?: Quote | null;
}

export interface SystemStatus {
  app_env: string;
  llm: {
    provider: string;
    model: string;
    analyst_model?: string | null;
    effort: string;
    is_stub: boolean;
  };
  data_providers: { name: string; healthy: boolean; capabilities: string[] }[];
  pipeline: { debate_rounds: number; timeout_sec: number };
  active_runs: number;
}

// ---------------------------------------------------------------- SSE 事件

export interface PipelineStage {
  stage: Stage;
  label: string;
  agents: { key: string; label: string }[];
}

export type AnalysisEvent =
  | { type: "run_start"; ts: number; run_id: string; symbol: string; name: string; horizon: string; as_of: string; data_sources: Record<string, string>; data_warnings: string[]; pipeline: PipelineStage[] }
  | { type: "stage_start"; ts: number; stage: Stage; label: string }
  | { type: "agent_start"; ts: number; agent: string; label: string; stage: Stage }
  | { type: "agent_delta"; ts: number; agent: string; text: string }
  | { type: "agent_thinking"; ts: number; agent: string; text: string }
  | { type: "agent_done"; ts: number; agent: string; output: AgentOutput }
  | { type: "decision"; ts: number; decision: Decision }
  | { type: "run_done"; ts: number; run_id: string; elapsed_sec: number; usage: Record<string, number>; agent_count: number; disclaimer: string; finished_at: string }
  | { type: "error"; ts: number; run_id?: string; message: string };
