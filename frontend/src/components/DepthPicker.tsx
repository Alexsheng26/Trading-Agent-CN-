"use client";

// 分析深度选择 + 高级里的模型指定，以及跑起来之后的"本次配置"一行字。
//
// 每一档具体改了什么由后端按当前模型厂商生成（GET /analysis/options）：
// 同样是"深度"，Claude 下是调高推理投入，DeepSeek 下是换推理模型 ——
// 前端写死的话，换了厂商说明就是错的。

import type { AnalysisOptions, Depth, RunConfig } from "@/lib/types";

// 后端没响应时的兜底：照样能选档位，只是不显示调用次数和说明
const FALLBACK: { value: Depth; label: string }[] = [
  { value: "quick", label: "快速" },
  { value: "standard", label: "标准" },
  { value: "deep", label: "深度" },
];

export function DepthPicker({
  options,
  depth,
  onDepth,
  model,
  onModel,
}: {
  options: AnalysisOptions | null;
  depth: Depth;
  onDepth: (d: Depth) => void;
  model: string;
  onModel: (m: string) => void;
}) {
  const depths: { value: Depth; label: string; llm_calls?: number }[] =
    options?.depths ?? FALLBACK;
  const selected = options?.depths.find((d) => d.value === depth);
  const models = options?.models ?? [];

  return (
    <div>
      <div className="text-sm font-medium">分析深度</div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {depths.map((d) => (
          <button
            key={d.value}
            type="button"
            onClick={() => onDepth(d.value)}
            aria-pressed={depth === d.value}
            className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
              depth === d.value
                ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                : "border-[var(--border)] bg-[var(--surface-2)]"
            }`}
          >
            <div className="text-sm font-medium">{d.label}</div>
            {d.llm_calls != null && (
              <div className="tabular mt-0.5 text-[10px] text-[var(--muted)]">
                {d.llm_calls} 次模型调用
              </div>
            )}
          </button>
        ))}
      </div>

      {selected && (
        <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted)]">
          {selected.blurb}：{selected.changes.join("；")}
        </p>
      )}
      {options?.notes.map((n) => (
        <p key={n} className="mt-1 text-[11px] leading-relaxed text-[var(--muted)]">
          {n}
        </p>
      ))}

      <details className="group mt-3 rounded-lg border border-[var(--border)] bg-[var(--surface-2)]">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-xs">
          <span>
            高级：指定模型
            <span className="ml-1 text-[var(--muted)]">
              {model ? `（${model}）` : "（按档位自动）"}
            </span>
          </span>
          <svg
            viewBox="0 0 16 16"
            className="h-3.5 w-3.5 shrink-0 text-[var(--muted)] transition-transform group-open:rotate-180"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          >
            <path d="M4 6l4 4 4-4" />
          </svg>
        </summary>
        <div className="border-t border-[var(--border)] px-3 py-3">
          {models.length >= 2 ? (
            <>
              <label className="sr-only" htmlFor="model-select">
                本次使用的模型
              </label>
              <select
                id="model-select"
                value={model}
                onChange={(e) => onModel(e.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
              >
                <option value="">按档位自动（推荐）</option>
                {models.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.value}
                    {m.note ? `（${m.note}）` : ""}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted)]">
                指定后，本次所有智能体都用这个模型；档位里的辩论轮数照常生效。{""}
                决策这一步如果它没给出可用结果，会自动改用主模型重做并注明。
              </p>
            </>
          ) : (
            <p className="text-[11px] leading-relaxed text-[var(--muted)]">
              {options?.provider === "echo" ? (
                "当前是离线桩，没有可选的模型。"
              ) : (
                <>
                  目前只有一个模型可用。在 <code>.env</code> 的 <code>LLM_MODEL_CHOICES</code>{" "}
                  里写上想用的模型名（逗号分隔），然后{options?.restart_hint ?? "重启后端"}，{""}
                  这里就会出现选项。
                </>
              )}
            </p>
          )}
        </div>
      </details>
    </div>
  );
}

/** 跑起来之后显示"这次用的是什么配置"。回看历史时靠它分清结论差异的来源。 */
export function RunConfigLine({ config }: { config: RunConfig | null }) {
  if (!config) return null;

  const parts: string[] = [
    `${config.depth_label}档`,
    `辩论 ${config.debate_rounds} 轮`,
    `${config.llm_calls} 次调用`,
  ];

  const { analyst_model: a, decision_model: d } = config;
  if (a && d) {
    parts.push(a === d ? `模型 ${a}` : `分析 ${a} · 决策 ${d}`);
  }
  if (config.model_override) parts.push("手动指定");

  const { analyst_effort: ae, decision_effort: de } = config;
  if (ae && de) {
    parts.push(ae === de ? `推理投入 ${ae}` : `推理投入 ${ae} / ${de}`);
  }

  return (
    <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted)]">{parts.join(" · ")}</p>
  );
}
