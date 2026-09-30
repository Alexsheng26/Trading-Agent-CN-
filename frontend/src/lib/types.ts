// 与后端 app/schemas 对应的类型定义。

export type Horizon = "短线" | "波段" | "中线";
export type Signal = "看多" | "中性" | "看空";
export type Action = "买入" | "加仓" | "持有" | "减仓" | "卖出" | "观望";
export type Stage = "analysts" | "debate" | "risk" | "decision";
/** 分析深度。后端按当前模型厂商翻译成辩论轮数、推理投入、模型 */
export type Depth = "quick" | "standard" | "deep";

/** 一次分析实际生效的参数（落库，回看时用来分清结论差异的来源） */
export interface RunConfig {
  depth: Depth;
  depth_label: string;
  provider: string;
  debate_rounds: number;
  llm_calls: number;
  analyst_model?: string | null;
  decision_model?: string | null;
  /** 只有 Claude 有推理投入，其余厂商为 null */
  analyst_effort?: string | null;
  decision_effort?: string | null;
  /** 用户手选的模型；null 表示按档位自动 */
  model_override?: string | null;
}

export interface DepthOption {
  value: Depth;
  label: string;
  blurb: string;
  debate_rounds: number;
  llm_calls: number;
  /** 这一档在当前厂商下实际改了什么（后端生成，前端不写死） */
  changes: string[];
  config: RunConfig;
}

export interface AnalysisOptions {
  provider: string;
  default_depth: Depth;
  depths: DepthOption[];
  /** 可手选的模型（白名单）。少于 2 个时前端不显示选择器 */
  models: { value: string; note: string }[];
  notes: string[];
}

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
  /** false = 这是最近一根日线的收盘价，不是实时价。界面必须显著标注 */
  is_realtime?: boolean;
  /** 非实时时对应的交易日 */
  quote_date?: string | null;
  /** stock | etf */
  asset_type?: "stock" | "etf";
  /** 价格小数位：股票 2，ETF 3（最小变动 0.001 元） */
  price_decimals?: number;
}

/** ETF 概况。ETF 没有财报，这些就是它的"基本面" */
export interface FundProfile {
  symbol: string;
  name: string;
  full_name: string;
  fund_type: string;
  /** 跟踪标的 */
  tracking_index: string;
  benchmark: string;
  manager: string;
  inception_date?: string | null;
  /** 资产规模（元） */
  asset_size?: number | null;
  asset_size_date?: string | null;
  /** 份额规模（份） */
  share_size?: number | null;
  /** 管理费率 %/年 */
  management_fee?: number | null;
  custody_fee?: number | null;
}

export interface FundHolding {
  code: string;
  name: string;
  /** 占净值比例 % */
  weight_pct?: number | null;
  /** 披露期，如 "2025年2季度" */
  period: string;
}

export interface FundInfo {
  symbol: string;
  profile: FundProfile | null;
  holdings: FundHolding[];
  /** 部分失败时的说明（档案和重仓股各自取、各自失败） */
  warnings: string[];
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

export type FibKind = "swing" | "retracement" | "extension";

export interface FibLevel {
  ratio: number;
  label: string;
  price: number;
  kind: FibKind;
}

export interface FibAnalysis {
  /** 上升 = 先低后高（回撤位是支撑）；下降 = 先高后低（回撤位是阻力） */
  direction: "上升" | "下降";
  swing_high: number;
  swing_low: number;
  swing_high_date?: string | null;
  swing_low_date?: string | null;
  lookback: number;
  /** 恒在 0-100：摆动端点即窗口极值，现价必然落在其间 */
  retracement_pct?: number | null;
  levels: FibLevel[];
  nearest_support?: FibLevel | null;
  nearest_resistance?: FibLevel | null;
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
  fib?: FibAnalysis | null;
  patterns: string[];
}

/** 交易时段。后端用固定 UTC+8 计算，不含法定节假日 */
export interface SessionInfo {
  phase: "盘前" | "集合竞价" | "连续竞价" | "午间休市" | "已收盘" | "非交易日";
  /** 价格是否可能变动。false 时前端不应轮询 */
  is_live: boolean;
  /** 建议轮询间隔（秒）。非交易时段为 null */
  poll_interval: number | null;
  /** 下一次时段变化的时刻（带 +08:00） */
  next_change: string | null;
  /** 服务端判断时所用的北京时间，用来校正浏览器时钟偏差 */
  as_of: string;
  note: string;
}

/** 本地代码目录状态。按名称搜索靠它 —— count=0 时只能按代码搜 */
export interface SymbolIndexStatus {
  count: number;
  updated_at: string | null;
  /** 超过 TTL，需要补一次新股 */
  stale: boolean;
  /** 后台正在重建 */
  refreshing: boolean;
  /** 目录数据来自哪些数据源 */
  sources: string[];
  /** 其中 ETF 有多少只。0 说明 ETF 列表还没拉成功，按名称搜不到 ETF */
  etf_count?: number;
}

export interface InterfaceProbe {
  name: string;
  status: "正常" | "空结果" | "失败" | "超时" | "不支持" | "跳过";
  detail: string;
}

export interface ProviderProbe {
  provider: string;
  dependency_ok: boolean;
  dependency_error?: string;
  interfaces: InterfaceProbe[];
}

/** 单个主机的可达性。分清"连不上域名"和"客户端被按指纹拒绝" */
export interface HostResult {
  host: string;
  used_by: string;
  /** 普通 requests 的结果 */
  plain: "正常" | "失败" | "未测";
  plain_detail: string;
  /** curl_cffi（浏览器指纹）的结果 */
  browser: "正常" | "失败" | "未测";
  browser_detail: string;
  verdict: string;
}

export interface TransportStatus {
  browser_impersonation: boolean;
  impersonate: string;
  /** 配置开着不等于真的生效（akshare 没装就不会） */
  active: boolean;
}

export interface Diagnosis {
  /** 后端版本号。排查时先确认跑的是哪一版 */
  app_version?: string;
  transport?: TransportStatus;
  hosts?: HostResult[];
  host_summary?: string;
  /** 探测时刻（北京时间） */
  checked_at: string;
  /** 数据源尝试顺序，前面的失败才降级到后面 */
  order: string[];
  providers: ProviderProbe[];
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
  /** 非空表示决策这一步换过模型重做 */
  fallback_note?: string;
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
  /** 档位与模型。v14 之前的老记录为 null */
  config?: RunConfig | null;
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
  depth?: Depth | null;
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
  app_version?: string;
  transport?: TransportStatus;
  /** 各维度最近一次实际命中的数据源。healthcheck 只说明依赖装好了，这个才是地面真相 */
  active_sources?: Record<string, string>;
  /** 是否有任何维度落到了模拟数据上 */
  using_mock_data?: boolean;
  llm: {
    provider: string;
    model: string;
    analyst_model?: string | null;
    effort: string;
    /** 兼容接口的推理模型（深度档的风控与决策用它） */
    reasoning_model?: string | null;
    /** 分析页能手选的模型 */
    model_choices?: string[];
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
  | { type: "run_start"; ts: number; run_id: string; symbol: string; name: string; horizon: string; as_of: string; data_sources: Record<string, string>; data_warnings: string[]; pipeline: PipelineStage[]; config?: RunConfig }
  | { type: "stage_start"; ts: number; stage: Stage; label: string }
  | { type: "agent_start"; ts: number; agent: string; label: string; stage: Stage }
  | { type: "agent_delta"; ts: number; agent: string; text: string }
  | { type: "agent_thinking"; ts: number; agent: string; text: string }
  | { type: "agent_done"; ts: number; agent: string; output: AgentOutput }
  | { type: "decision"; ts: number; decision: Decision }
  | { type: "run_done"; ts: number; run_id: string; elapsed_sec: number; usage: Record<string, number>; agent_count: number; disclaimer: string; finished_at: string }
  | { type: "error"; ts: number; run_id?: string; message: string };
