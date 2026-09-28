"use client";

// 数据源自助诊断。
//
// 存在的理由：healthcheck 只检查"依赖装上了没有"，接口全挂时它照样返回可用。
// 真出问题时想知道到底哪一步失败、为什么失败，以前只能去翻容器日志 ——
// 对着 docker compose logs 找一行 akshare 报错，这事不该让用户做。

import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Diagnosis, InterfaceProbe } from "@/lib/types";

const STATUS_TONE: Record<InterfaceProbe["status"], string> = {
  正常: "bg-[var(--down-soft)] text-[var(--down)]",
  空结果: "bg-[var(--warn-soft)] text-[var(--warn)]",
  失败: "bg-[var(--up-soft)] text-[var(--up)]",
  超时: "bg-[var(--up-soft)] text-[var(--up)]",
  不支持: "bg-[var(--surface-2)] text-[var(--muted)]",
  跳过: "bg-[var(--surface-2)] text-[var(--muted)]",
};

export function DiagnosePanel() {
  const [result, setResult] = useState<Diagnosis | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    controller.current?.abort();
    const ctrl = new AbortController();
    controller.current = ctrl;
    setRunning(true);
    setError(null);
    try {
      setResult(await api.diagnose(ctrl.signal));
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "探测失败");
    } finally {
      if (controller.current === ctrl) {
        controller.current = null;
        setRunning(false);
      }
    }
  }, []);

  return (
    <div className="p-4">
      <p className="text-xs leading-relaxed text-[var(--muted)]">
        逐个真实调用每个数据源的接口，告诉你<strong className="text-[var(--text)]">哪一步失败、
        为什么失败</strong>。搜索出不来结果、价格一直是假数据，先跑这个。
      </p>
      <button
        type="button"
        onClick={() => void run()}
        disabled={running}
        className="mt-3 rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
      >
        {running ? "探测中…（最多约 1 分钟）" : result ? "重新探测" : "开始探测"}
      </button>
      {running && (
        <p className="mt-2 text-[11px] text-[var(--muted)]">
          会真的发网络请求，单个接口最多等 12 秒。请不要关闭页面。
        </p>
      )}

      {error && (
        <div className="mt-3 rounded-lg bg-[var(--up-soft)] px-3 py-2 text-xs text-[var(--up)]">
          {error}
        </div>
      )}

      {result && !running && (
        <div className="mt-4 space-y-3">
          <p className="text-[11px] text-[var(--muted)]">
            尝试顺序：{result.order.join(" → ")} · 探测于{" "}
            {result.checked_at.slice(11, 19)}
          </p>

          {result.providers.map((p) => (
            <div key={p.provider} className="rounded-lg border border-[var(--border)]">
              <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2">
                <span className="text-sm font-medium">{p.provider}</span>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${
                    p.dependency_ok
                      ? "bg-[var(--down-soft)] text-[var(--down)]"
                      : "bg-[var(--up-soft)] text-[var(--up)]"
                  }`}
                >
                  {p.dependency_ok ? "依赖就绪" : "依赖不可用"}
                </span>
              </div>

              {p.dependency_error && (
                <p className="border-b border-[var(--border)] px-3 py-2 text-[11px] text-[var(--up)]">
                  {p.dependency_error}
                </p>
              )}

              <ul className="divide-y divide-[var(--border)]">
                {p.interfaces.map((iface) => (
                  <li key={iface.name} className="px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs">{iface.name}</span>
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${STATUS_TONE[iface.status]}`}
                      >
                        {iface.status}
                      </span>
                    </div>
                    {iface.detail && iface.status !== "正常" && (
                      <p className="mt-1 break-words text-[10px] leading-relaxed text-[var(--muted)]">
                        {iface.detail}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}

          <Advice result={result} />
        </div>
      )}
    </div>
  );
}

/** 把探测结果翻译成"接下来该做什么"。报一堆状态而不给下一步等于没说。 */
function Advice({ result }: { result: Diagnosis }) {
  const real = result.providers.filter((p) => p.provider !== "mock");
  const broken = real.filter((p) =>
    p.interfaces.some((i) => i.status === "失败" || i.status === "超时"),
  );
  const noneOk =
    real.length > 0 &&
    real.every((p) => !p.interfaces.some((i) => i.status === "正常"));

  if (real.length === 0) {
    return (
      <Tip tone="warn">
        当前只启用了 mock 模拟数据源，界面上的价格全是程序生成的假数据。在{" "}
        <code>.env</code> 里设 <code>DATA_PROVIDER_ORDER=akshare,tushare</code> 后重启后端。
      </Tip>
    );
  }
  if (noneOk) {
    return (
      <Tip tone="warn">
        所有真实数据源都取不到数据，界面会自动降级到模拟数据。常见原因：
        <br />· 机器上不去外网，或公司网络/防火墙拦了行情站点；
        <br />· AKShare 靠抓取网页，对方改版或限流时会整体失效 —— 换{" "}
        <code>DATA_PROVIDER_ORDER=tushare,akshare</code>，Tushare 是正式接口，免费注册后
        在 <code>.env</code> 里填 <code>TUSHARE_TOKEN</code> 即可；
        <br />· 上面的失败原因里若出现 403 / timeout，基本就是网络问题。
      </Tip>
    );
  }
  if (broken.length > 0) {
    return (
      <Tip tone="warn">
        {broken.map((p) => p.provider).join("、")} 有接口失败，但还有可用的源 ——
        系统会逐接口降级，功能不会整体中断。想彻底修好就按上面的失败原因处理。
      </Tip>
    );
  }
  return <Tip tone="ok">全部接口正常，行情数据是真实的。</Tip>;
}

function Tip({ tone, children }: { tone: "ok" | "warn"; children: React.ReactNode }) {
  const cls =
    tone === "ok"
      ? "bg-[var(--down-soft)] text-[var(--down)]"
      : "bg-[var(--warn-soft)] text-[var(--warn)]";
  return <div className={`rounded-lg px-3 py-2 text-[11px] leading-relaxed ${cls}`}>{children}</div>;
}
