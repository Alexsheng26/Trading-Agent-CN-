"use client";

// 设置页：只展示后端的实际配置状态（配置本身在 .env 里改）。

import { useEffect, useState } from "react";
import { DiagnosePanel } from "@/components/DiagnosePanel";
import { SymbolIndexPanel } from "@/components/SymbolIndexPanel";
import { api } from "@/lib/api";
import type { SystemStatus } from "@/lib/types";

const RUNTIME_LABELS: Record<string, string> = {
  docker: "Docker",
  windows: "Windows 原生",
  dev: "开发模式",
};

export default function SettingsPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .status()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : "无法连接后端"));
  }, []);

  if (error) {
    return (
      <div className="mx-auto w-full max-w-2xl px-4 py-6 md:px-6">
        <div className="rounded-xl bg-[var(--up-soft)] px-4 py-3 text-sm text-[var(--up)]">
          {error}
          <p className="mt-2 text-xs text-[var(--muted)]">
            确认后端已启动：Windows 上双击 <code>start-windows.cmd</code>，看它打开的窗口里有没有报错；{""}
            Docker 用 <code>docker compose up -d</code>。
          </p>
        </div>
      </div>
    );
  }

  if (!status) {
    return <div className="px-4 py-6 text-sm text-[var(--muted)] md:px-6">加载中…</div>;
  }

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-4 md:px-6 md:py-6">
      <div className="mb-4 flex items-baseline gap-2">
        <h1 className="text-lg font-semibold">设置与状态</h1>
        {/* 版本号放在标题旁：排查时第一件要确认的就是跑的是哪一版 */}
        {status.app_version && (
          <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">
            {status.app_version}
            {status.runtime && ` · ${RUNTIME_LABELS[status.runtime] ?? status.runtime}`}
          </span>
        )}
      </div>

      {status.llm.is_stub && (
        <div className="mb-4 rounded-xl bg-[var(--warn-soft)] p-4 text-sm text-[var(--warn)]">
          <strong>当前是离线桩模式</strong>
          <p className="mt-1.5 text-xs leading-relaxed">
            分析结果是模板占位文本，模拟数据也是程序生成的假数据，<strong>没有任何参考价值</strong>。
            要跑真实分析，在 <code>.env</code> 里配置：
          </p>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-[var(--bg)] p-3 text-[11px] text-[var(--text)]">
{`LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
DATA_PROVIDER_ORDER=akshare,tushare`}
          </pre>
        </div>
      )}

      <Section title="模型">
        <Row label="后端" value={providerLabel(status.llm.provider)} />
        <Row label="决策模型" value={status.llm.model} />
        {status.llm.analyst_model && <Row label="分析师模型" value={status.llm.analyst_model} />}
        {status.llm.provider === "anthropic" ? (
          <Row label="推理投入（标准档）" value={status.llm.effort} />
        ) : (
          status.llm.provider === "openai_compat" && (
            <Row
              label="推理模型"
              value={status.llm.reasoning_model || "未配置"}
              tone={status.llm.reasoning_model ? "normal" : "warn"}
              hint="深度档的风控与决策用它"
            />
          )
        )}
        {!status.llm.is_stub && (
          <Row
            label="可手选的模型"
            value={
              status.llm.model_choices && status.llm.model_choices.length > 1
                ? `${status.llm.model_choices.length} 个`
                : "只有主模型"
            }
            hint="LLM_MODEL_CHOICES"
          />
        )}
        <p className="px-4 py-3 text-[11px] leading-relaxed text-[var(--muted)]">
          每次分析可以在分析页选「快速 / 标准 / 深度」，这里显示的是标准档的默认值。{""}
          {status.llm.provider === "openai_compat" &&
            "DeepSeek 这类兼容接口没有“推理投入”参数，档位靠辩论轮数和推理模型区分。"}
        </p>
      </Section>

      <Section title="数据源">
        {status.data_providers.map((p) => (
          <Row
            key={p.name}
            label={p.name}
            value={p.healthy ? "可用" : "不可用"}
            tone={p.healthy ? "ok" : "warn"}
            hint={`${p.capabilities.length} 个接口`}
          />
        ))}
        <p className="mt-2 px-1 text-[11px] leading-relaxed text-[var(--muted)]">
          按顺序尝试，前面的不可用时自动降级到后面。单个接口不支持也会逐项降级
          —— 比如 Tushare 没有个股新闻，新闻就会自动走 AKShare。
          <br />
          推荐 <code>akshare,xueqiu,sina,tushare</code>：这几个源
          <strong className="text-[var(--text)]">主机互不相同</strong>，
          东方财富在某些网络下不可达时（代理软件、公司网络），
          行情和 K 线能自动落到雪球和新浪上。
        </p>
      </Section>

      <Section title="股票代码目录">
        <SymbolIndexPanel />
      </Section>

      <Section title="数据源诊断">
        <DiagnosePanel />
      </Section>

      <Section title="流水线">
        <Row
          label="多空辩论轮数（标准档）"
          value={String(status.pipeline.debate_rounds)}
          hint={`快速 ${Math.min(1, status.pipeline.debate_rounds)} · 深度 ${status.pipeline.debate_rounds + 1}`}
        />
        <Row
          label="单次超时（标准档）"
          value={`${status.pipeline.timeout_sec} 秒`}
          hint="深度档 ×1.5"
        />
        <Row label="进行中的分析" value={String(status.active_runs)} />
      </Section>

      <Section title="装到手机上">
        <p className="px-1 text-xs leading-relaxed text-[var(--muted)]">
          <strong className="text-[var(--text)]">iPhone</strong>：Safari 打开本站 → 分享 →
          「添加到主屏幕」。
          <br />
          <strong className="text-[var(--text)]">Android</strong>：Chrome 打开本站 → 菜单 →
          「安装应用」。
          <br />
          <br />
          装好后会像原生 App 一样全屏打开。局域网内用手机访问电脑的地址即可，
          例如 <code>http://192.168.1.8:3000</code>（把 IP 换成运行本服务的电脑 IP，
          并把该地址加进后端的 <code>CORS_ORIGINS</code>）。
        </p>
      </Section>

      <p className="mt-6 rounded-lg bg-[var(--surface-2)] p-3 text-[11px] leading-relaxed text-[var(--muted)]">
        本工具的全部输出由 AI 模型生成，仅供研究参考，
        <strong className="text-[var(--warn)]">不构成投资建议</strong>。
        模型可能产生事实错误，数据源可能延迟或错漏。证券投资有本金损失风险，请独立决策。
      </p>
    </div>
  );
}

function providerLabel(provider: string): string {
  return (
    {
      anthropic: "Claude（Anthropic）",
      openai_compat: "OpenAI 兼容接口",
      echo: "离线桩（不联网）",
    }[provider] ?? provider
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-4">
      <h2 className="mb-2 text-sm font-medium text-[var(--muted)]">{title}</h2>
      <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
        {children}
      </div>
    </section>
  );
}

function Row({
  label,
  value,
  hint,
  tone = "normal",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "normal" | "ok" | "warn";
}) {
  const color =
    tone === "ok" ? "text-[var(--down)]" : tone === "warn" ? "text-[var(--warn)]" : "text-[var(--text)]";
  return (
    <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-3 text-sm last:border-b-0">
      <span className="text-[var(--muted)]">{label}</span>
      <span className="text-right">
        <span className={color}>{value}</span>
        {hint && <span className="ml-2 text-[11px] text-[var(--muted)]">{hint}</span>}
      </span>
    </div>
  );
}
