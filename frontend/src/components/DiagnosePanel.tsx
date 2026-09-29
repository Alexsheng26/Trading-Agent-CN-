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
  const mockInChain = result.order.includes("mock");

  // 关键：**按接口**判断，不是按数据源判断。
  // 之前这里只看"某个真实源有没有任何一个接口正常"——akshare 只有新闻能通
  // 也算"有可用的源"，于是提示说"功能不会整体中断"，而实际上价格、K线、
  // 搜索全部落到了 mock 上，用户看到的是一屏假数据。
  const realOk = new Set<string>();
  for (const p of real) {
    for (const i of p.interfaces) {
      if (i.status === "正常") realOk.add(i.name);
    }
  }
  const CORE = ["实时行情", "日线", "搜索"];
  const missingCore = CORE.filter((name) => !realOk.has(name));

  // 连接被对方直接掐断（而不是超时）是 TLS 指纹拦截的典型特征
  const disconnected = real.some((p) =>
    p.interfaces.some(
      (i) => /RemoteDisconnected|Connection aborted|ConnectionResetError/.test(i.detail),
    ),
  );

  if (real.length === 0) {
    return (
      <Tip tone="warn">
        当前只启用了 mock 模拟数据源，界面上的价格全是程序生成的假数据。在{" "}
        <code>.env</code> 里设 <code>DATA_PROVIDER_ORDER=akshare,tushare</code>，
        然后 <code>docker compose up -d</code>（注意不是 <code>restart</code>，
        见下）。
      </Tip>
    );
  }

  if (mockInChain && missingCore.length > 0) {
    return (
      <Tip tone="danger">
        <strong>你现在看到的 {missingCore.join("、")} 是假数据。</strong>
        <br />
        真实数据源取不到这几项，而 <code>mock</code> 还在数据源链里
        （尝试顺序：{result.order.join(" → ")}），于是自动降级到了程序生成的
        假数字 —— 名称会是「模拟股份XXX」，价格、涨跌幅、AI 给出的买卖点位
        <strong>全部没有参考价值</strong>。
        <br />
        <br />
        第一步先把假数据关掉：<code>.env</code> 里改成{" "}
        <code>DATA_PROVIDER_ORDER=akshare,tushare</code>，然后执行{" "}
        <code>docker compose up -d</code>。
        <br />
        <strong>不要用 <code>docker compose restart</code></strong> ——
        它只是重启现有容器，不会重新读取 <code>.env</code>，改了也不生效。
        改完回这里再探测一次，最上面那行「尝试顺序」里不该再有 mock。
        <br />
        <br />
        关掉之后取不到数就会明确报错（而不是给你假数字），接着按下面的失败原因处理。
      </Tip>
    );
  }

  if (missingCore.length > 0) {
    return (
      <Tip tone="warn">
        真实数据源取不到 {missingCore.join("、")}，界面会明确显示取数失败 ——
        这是对的，本工具不会用假数字填充。
        {disconnected && (
          <>
            <br />
            <br />
            失败原因里出现了「对方直接掐断连接」（RemoteDisconnected /
            Connection aborted）。这不是超时，而是服务端在 TLS 握手层面
            拒绝了普通 Python 客户端。本项目已默认启用浏览器指纹伪装
            （<code>AKSHARE_BROWSER_TRANSPORT=true</code>）来绕过这类拦截；
            如果开着还是这样，基本可以确定是本机网络层面的问题 ——
            代理软件、公司网络、安全软件拦了 <code>push2.eastmoney.com</code>。
          </>
        )}
        <br />
        <br />
        最省心的替代方案是换 Tushare：它是正式 API 而不是抓网页，稳定得多。
        去 <code>tushare.pro</code> 免费注册拿 token，在 <code>.env</code> 里设{" "}
        <code>TUSHARE_TOKEN=你的token</code> 和{" "}
        <code>DATA_PROVIDER_ORDER=tushare,akshare</code>，然后{" "}
        <code>docker compose up -d</code>。
      </Tip>
    );
  }

  const broken = real.filter((p) =>
    p.interfaces.some((i) => i.status === "失败" || i.status === "超时"),
  );
  if (broken.length > 0) {
    return (
      <Tip tone="ok">
        核心行情（实时行情、日线、搜索）都能从真实数据源取到。
        {broken.map((p) => p.provider).join("、")} 有个别接口失败，
        系统会逐接口降级到其他源，不影响使用。
      </Tip>
    );
  }
  return <Tip tone="ok">全部接口正常，行情数据是真实的。</Tip>;
}

function Tip({
  tone,
  children,
}: {
  tone: "ok" | "warn" | "danger";
  children: React.ReactNode;
}) {
  const cls =
    tone === "ok"
      ? "bg-[var(--down-soft)] text-[var(--down)]"
      : tone === "danger"
        ? "bg-[var(--up-soft)] text-[var(--up)] border border-[var(--up)]"
        : "bg-[var(--warn-soft)] text-[var(--warn)]";
  return <div className={`rounded-lg px-3 py-2 text-[11px] leading-relaxed ${cls}`}>{children}</div>;
}
