"use client";

// 订阅一次分析的 SSE 事件流，并把事件聚合成可直接渲染的状态。
//
// 手机端的几个关键处理：
// 1. **断线自动重连**：原生 EventSource 会自动重连，并把 Last-Event-ID 带上，
//    后端据此续传，不重不漏。
// 2. **锁屏/切后台**：iOS Safari 会冻结页面，回到前台时连接可能已死。
//    监听 visibilitychange，回前台时主动校验一次状态。
// 3. **兜底拉取**：连接彻底失败时改用 REST 轮询读落库结果 ——
//    分析本身在服务端照跑，不会因为前端断了就白跑。

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  AgentOutput,
  AnalysisEvent,
  Decision,
  PipelineStage,
  RunConfig,
  Stage,
} from "./types";

export interface LiveAgent {
  key: string;
  label: string;
  stage: Stage;
  /** pending 尚未开始 / streaming 正在输出 / done 已完成 / error 失败 */
  status: "pending" | "streaming" | "done" | "error";
  content: string;
  thinking: string;
  signal?: string | null;
  confidence?: number | null;
  error?: string;
}

export interface StreamState {
  connection: "idle" | "connecting" | "live" | "polling" | "closed";
  runId: string | null;
  symbol: string;
  name: string;
  pipeline: PipelineStage[];
  agents: Record<string, LiveAgent>;
  currentStage: Stage | null;
  decision: Decision | null;
  dataSources: Record<string, string>;
  dataWarnings: string[];
  elapsedSec: number | null;
  usage: Record<string, number> | null;
  /** 这次用的档位与模型 */
  config: RunConfig | null;
  finished: boolean;
  error: string | null;
}

const EMPTY: StreamState = {
  connection: "idle",
  runId: null,
  symbol: "",
  name: "",
  pipeline: [],
  agents: {},
  currentStage: null,
  decision: null,
  dataSources: {},
  dataWarnings: [],
  elapsedSec: null,
  usage: null,
  config: null,
  finished: false,
  error: null,
};

const STAGE_LABELS: Record<Stage, string> = {
  analysts: "多维度分析",
  debate: "多空辩论",
  risk: "风险评估",
  decision: "交易决策",
};
const STAGE_ORDER: Stage[] = ["analysts", "debate", "risk", "decision"];

/**
 * 从落库的产出反推流水线骨架。
 *
 * 骨架本来来自 run_start 事件，但那只在实时事件缓冲里。缓冲过期或
 * 后端重启过之后打开历史记录，走的是 REST 轮询，拿不到 run_start ——
 * 没有这一步的话页面只剩决策面板，各智能体的正文一张都不显示。
 */
function pipelineFromOutputs(outputs: AgentOutput[], hasDecision: boolean): PipelineStage[] {
  const out: PipelineStage[] = [];
  for (const stage of STAGE_ORDER) {
    const agents = outputs
      .filter((o) => o.stage === stage)
      .map((o) => ({ key: o.key, label: o.label }));
    if (stage === "decision" && hasDecision) agents.push({ key: "trader", label: "交易决策员" });
    if (agents.length > 0) out.push({ stage, label: STAGE_LABELS[stage], agents });
  }
  return out;
}

/** 把一条事件叠加到当前状态上。纯函数，方便单独推理。 */
function reduce(state: StreamState, event: AnalysisEvent): StreamState {
  switch (event.type) {
    case "run_start": {
      // 先按 pipeline 把所有智能体建成 pending，界面立刻有骨架
      const agents: Record<string, LiveAgent> = {};
      for (const stage of event.pipeline) {
        for (const a of stage.agents) {
          agents[a.key] = {
            key: a.key,
            label: a.label,
            stage: stage.stage,
            status: "pending",
            content: "",
            thinking: "",
          };
        }
      }
      return {
        ...state,
        runId: event.run_id,
        symbol: event.symbol,
        name: event.name,
        pipeline: event.pipeline,
        agents,
        dataSources: event.data_sources ?? {},
        dataWarnings: event.data_warnings ?? [],
        config: event.config ?? state.config,
      };
    }

    case "stage_start":
      return { ...state, currentStage: event.stage };

    case "agent_start": {
      const prev = state.agents[event.agent];
      return {
        ...state,
        agents: {
          ...state.agents,
          [event.agent]: {
            key: event.agent,
            label: event.label,
            stage: event.stage,
            status: "streaming",
            content: prev?.content ?? "",
            thinking: prev?.thinking ?? "",
          },
        },
      };
    }

    case "agent_delta": {
      const prev = state.agents[event.agent];
      if (!prev) return state;
      return {
        ...state,
        agents: {
          ...state.agents,
          [event.agent]: { ...prev, status: "streaming", content: prev.content + event.text },
        },
      };
    }

    case "agent_thinking": {
      const prev = state.agents[event.agent];
      if (!prev) return state;
      return {
        ...state,
        agents: {
          ...state.agents,
          [event.agent]: { ...prev, thinking: prev.thinking + event.text },
        },
      };
    }

    case "agent_done": {
      const o: AgentOutput = event.output;
      return {
        ...state,
        agents: {
          ...state.agents,
          [event.agent]: {
            key: o.key,
            label: o.label,
            stage: o.stage,
            status: o.error ? "error" : "done",
            // 用最终正文替换流式累积的内容（尾标已被后端剥掉）
            content: o.content,
            thinking: o.thinking,
            signal: o.signal,
            confidence: o.confidence,
            error: o.error,
          },
        },
      };
    }

    case "decision": {
      // 决策走单独的 decision 事件（不是 agent_done），
      // 所以要手动把"交易决策员"那张卡标成完成，否则它会一直停在"等待中…"。
      const trader = state.agents["trader"];
      const agents = trader
        ? {
            ...state.agents,
            trader: {
              ...trader,
              status: "done" as const,
              content: event.decision.summary || "决策已生成，详见上方决策面板。",
            },
          }
        : state.agents;
      return { ...state, agents, decision: event.decision, currentStage: "decision" };
    }

    case "run_done":
      return {
        ...state,
        finished: true,
        connection: "closed",
        elapsedSec: event.elapsed_sec,
        usage: event.usage,
      };

    case "error":
      return { ...state, error: event.message, finished: true, connection: "closed" };

    default:
      return state;
  }
}

export function useAnalysisStream(runId: string | null) {
  const [state, setState] = useState<StreamState>(EMPTY);
  const sourceRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** SSE 不可用时的兜底：直接读落库结果。 */
  const fallbackToPolling = useCallback((id: string) => {
    if (pollRef.current) return;
    setState((s) => ({ ...s, connection: "polling" }));

    const tick = async () => {
      try {
        const run = await api.getRun(id);
        setState((s) => {
          const agents = { ...s.agents };
          for (const o of run.outputs) {
            agents[o.key] = {
              key: o.key,
              label: o.label,
              stage: o.stage,
              status: o.error ? "error" : "done",
              content: o.content,
              thinking: o.thinking,
              signal: o.signal,
              confidence: o.confidence,
              error: o.error,
            };
          }
          const decision = run.decision ?? s.decision;
          if (decision) {
            // 历史记录走这里时没有 run_start，决策员的卡片要自己补上
            const prevTrader = agents["trader"] as LiveAgent | undefined;
            agents["trader"] = {
              key: "trader",
              label: prevTrader?.label ?? "交易决策员",
              stage: "decision",
              thinking: prevTrader?.thinking ?? "",
              status: "done",
              content: decision.summary || "决策已生成，详见上方决策面板。",
            };
          }
          const pipeline =
            s.pipeline.length > 0 ? s.pipeline : pipelineFromOutputs(run.outputs, Boolean(decision));
          const done = run.status === "done" || run.status === "error" || run.status === "cancelled";
          return {
            ...s,
            runId: run.id,
            symbol: run.symbol,
            name: run.name || s.name,
            pipeline,
            agents,
            decision,
            dataSources: run.data_sources ?? s.dataSources,
            dataWarnings: run.data_warnings ?? s.dataWarnings,
            elapsedSec: run.elapsed_sec ?? s.elapsedSec,
            usage: run.usage ?? s.usage,
            config: run.config ?? s.config,
            finished: done,
            error: run.error || s.error,
            connection: done ? "closed" : "polling",
          };
        });

        if (
          pollRef.current &&
          (run.status === "done" || run.status === "error" || run.status === "cancelled")
        ) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        // 单次轮询失败无所谓，下一次再试
      }
    };

    void tick();
    pollRef.current = setInterval(tick, 2500);
  }, []);

  useEffect(() => {
    if (!runId) {
      setState(EMPTY);
      return;
    }

    setState({ ...EMPTY, runId, connection: "connecting" });

    const source = new EventSource(`/api/v1/analysis/runs/${runId}/stream`);
    sourceRef.current = source;

    source.onopen = () => setState((s) => ({ ...s, connection: "live", error: null }));

    // 后端把类型放在 SSE 的 event 字段里，但也一并写进了 data.type，
    // 所以统一用 onmessage 之外的具名监听更稳妥。
    const handle = (raw: MessageEvent) => {
      let event: AnalysisEvent;
      try {
        event = JSON.parse(raw.data) as AnalysisEvent;
      } catch {
        return;
      }
      setState((s) => reduce(s, event));

      if (event.type === "run_done" || event.type === "error") {
        source.close();
      }
    };

    const eventNames = [
      "run_start",
      "stage_start",
      "agent_start",
      "agent_delta",
      "agent_thinking",
      "agent_done",
      "decision",
      "run_done",
      "error",
    ];
    for (const name of eventNames) source.addEventListener(name, handle);

    source.onerror = () => {
      // EventSource 会自己带 Last-Event-ID 重连。
      // 只有它彻底关闭（CLOSED）才说明没救了，这时转轮询。
      if (source.readyState === EventSource.CLOSED) {
        setState((s) => (s.finished ? s : { ...s, connection: "polling" }));
        fallbackToPolling(runId);
      } else {
        setState((s) => (s.finished ? s : { ...s, connection: "connecting" }));
      }
    };

    // iOS 锁屏回来后连接可能已死而没触发 error，主动校验一次
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (source.readyState === EventSource.CLOSED) {
        setState((s) => (s.finished ? s : { ...s, connection: "polling" }));
        fallbackToPolling(runId);
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      for (const name of eventNames) source.removeEventListener(name, handle);
      source.close();
      sourceRef.current = null;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [runId, fallbackToPolling]);

  return state;
}

export { reduce as reduceAnalysisEvent, EMPTY as emptyStreamState };
