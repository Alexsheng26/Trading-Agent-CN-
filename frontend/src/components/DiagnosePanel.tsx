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
          会真的发网络请求：各数据源和主机可达性同时探测，同一个数据源的接口逐个调用（单个最多 12 秒）。请不要关闭页面。
        </p>
      )}

      {error && (
        <div className="mt-3 rounded-lg bg-[var(--up-soft)] px-3 py-2 text-xs text-[var(--up)]">
          {error}
        </div>
      )}

      {result && !running && (
        <div className="mt-4 space-y-3">
          <div className="space-y-1 text-[11px] text-[var(--muted)]">
            <p>
              尝试顺序：{result.order.join(" → ")} · 探测于{" "}
              {result.checked_at.slice(11, 19)}
            </p>
            {/* 版本和指纹状态摆在最显眼处：排查时第一件要确认的就是
                "跑的是哪一版、指纹到底有没有生效" */}
            <p>
              后端版本 <strong className="text-[var(--text)]">{result.app_version ?? "未知"}</strong>
              {result.transport && (
                <>
                  {" · 浏览器指纹 "}
                  <strong
                    className={
                      result.transport.active
                        ? "text-[var(--down)]"
                        : "text-[var(--warn)]"
                    }
                  >
                    {result.transport.active
                      ? `已生效（${result.transport.impersonate}）`
                      : result.transport.browser_impersonation
                        ? "配置开启但未生效"
                        : "已关闭"}
                  </strong>
                </>
              )}
            </p>
          </div>

          {result.hosts && result.hosts.length > 0 && <HostTable result={result} />}

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
          <EtfAdvice result={result} />
          <GoldAdvice result={result} />
        </div>
      )}
    </div>
  );
}

/** 主机可达性。两种客户端各打一次，才能分清"连不上"和"被按指纹拒绝"。 */
function HostTable({ result }: { result: Diagnosis }) {
  const hosts = result.hosts ?? [];
  return (
    <div className="rounded-lg border border-[var(--border)]">
      <div className="border-b border-[var(--border)] px-3 py-2">
        <span className="text-sm font-medium">主机可达性</span>
        <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
          每个主机用普通 requests 和浏览器指纹各打一次。两个都失败 = 这台机器
          连不上该域名；只有普通 requests 失败 = 被按客户端指纹拦了。
        </p>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {hosts.map((h) => (
          <li key={h.host} className="px-3 py-2">
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate text-xs">
                {h.host}
                <span className="ml-2 text-[10px] text-[var(--muted)]">{h.used_by}</span>
              </span>
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${
                  h.verdict === "可达"
                    ? "bg-[var(--down-soft)] text-[var(--down)]"
                    : h.verdict.startsWith("指纹")
                      ? "bg-[var(--warn-soft)] text-[var(--warn)]"
                      : "bg-[var(--up-soft)] text-[var(--up)]"
                }`}
              >
                {h.verdict}
              </span>
            </div>
            <p className="tabular mt-1 text-[10px] text-[var(--muted)]">
              普通 {h.plain}
              {h.plain !== "正常" && h.plain_detail ? `（${h.plain_detail}）` : ""} · 指纹{" "}
              {h.browser}
              {h.browser !== "正常" && h.browser_detail ? `（${h.browser_detail}）` : ""}
            </p>
          </li>
        ))}
      </ul>
      {result.host_summary && (
        <p className="border-t border-[var(--border)] px-3 py-2 text-[11px] leading-relaxed text-[var(--text)]">
          {result.host_summary}
        </p>
      )}
    </div>
  );
}

/**
 * "改完 .env 之后"的那一步。按后端报告的运行方式写：
 * 不用 Docker 的人照着 docker 命令做，只会得到"docker 不是内部或外部命令"。
 */
function RestartStep({ result }: { result: Diagnosis }) {
  if (result.runtime === "docker") {
    return (
      <>
        执行 <code>docker compose up -d</code>（不要用 <code>restart</code>：它不会重新读取{" "}
        <code>.env</code>，改了也不生效）
      </>
    );
  }
  // 老版本后端没有这个字段
  return <>{result.restart_hint ?? "重启后端"}</>;
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
  // 只有行情和 K 线是真正的核心：缺了界面就没数据。
  // 搜索不算 —— 按名称搜索走的是本地代码目录，全市场列表接口天生要一分多钟，
  // 它超时是预期行为，把它算进"核心缺失"会让用户去追一个不存在的问题。
  const CORE = ["实时行情", "日线"];
  const missingCore = CORE.filter((name) => !realOk.has(name));
  const searchDown = !realOk.has("搜索");

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
        <code>.env</code> 里设 <code>DATA_PROVIDER_ORDER=akshare,tushare</code>，然后
        <RestartStep result={result} />。
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
        <code>DATA_PROVIDER_ORDER=akshare,tushare</code>，然后<RestartStep result={result} />。
        <br />
        改完回这里再探测一次，最上面那行「尝试顺序」里不该再有 mock。
        <br />
        <br />
        关掉之后取不到数就会明确报错（而不是给你假数字），接着按下面的失败原因处理。
        {disconnected && (
          <>
            <br />
            <br />
            顺带说一下真源为什么不通：失败原因里是「对方直接掐断连接」
            （RemoteDisconnected / Connection aborted），<strong>不是超时</strong> ——
            服务端在 TLS 握手层面拒绝了普通 Python 客户端。本项目已默认启用
            浏览器指纹伪装（<code>AKSHARE_BROWSER_TRANSPORT=true</code>）绕过它。
          </>
        )}
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
        <code>DATA_PROVIDER_ORDER=tushare,akshare</code>，然后<RestartStep result={result} />。
      </Tip>
    );
  }

  const broken = real.filter((p) =>
    p.interfaces.some((i) => i.status === "失败" || i.status === "超时"),
  );
  if (broken.length > 0) {
    return (
      <Tip tone="ok">
        <strong>行情和 K 线都能从真实数据源取到</strong>，数据是真的。
        {broken.map((p) => p.provider).join("、")} 有个别接口失败，
        系统会逐接口降级到其他源，不影响使用。
        {searchDown && (
          <>
            <br />
            <br />
            其中「搜索」超时/失败是<strong>预期行为</strong>：全市场列表接口要翻
            50 多页、每页还要等 1 秒，本身就需要一分多钟，诊断只给它 12 秒。
            按名称搜索并不实时调它 —— 走的是上面「股票代码目录」那份本地表，
            只要那里显示了股票数量就是可用的。
          </>
        )}
      </Tip>
    );
  }
  return <Tip tone="ok">全部接口正常，行情数据是真实的。</Tip>;
}

/** ETF 单独给一句结论：它的数据来源和股票不一样，混在一起说会让人看不懂。 */
function EtfAdvice({ result }: { result: Diagnosis }) {
  const real = result.providers.filter((p) => p.provider !== "mock");
  const ok = new Set<string>();
  let probed = false;
  for (const p of real) {
    for (const i of p.interfaces) {
      if (i.name.startsWith("ETF")) probed = true;
      if (i.status === "正常") ok.add(i.name);
    }
  }
  if (!probed) return null; // 老版本后端没有 ETF 探测

  const missingCore = ["ETF 行情", "ETF 日线"].filter((n) => !ok.has(n));
  const missingExtra = ["ETF 概况", "ETF 持仓"].filter((n) => !ok.has(n));
  // 东财盘口接口会把沪市 ETF（5 开头）查成深市，akshare 那一行必然失败 —— 这是预期的
  const expectedNote = (
    <>
      akshare 那一行的「ETF 行情」失败是<strong>预期的</strong>：
      {"东财的盘口接口按「6 开头」判断沪市，会把 512480 这类沪市 ETF 查成深市，所以直接跳过，"}
      {"ETF 实时价由新浪（一个请求拿全部 ETF）和雪球提供。"}
    </>
  );

  if (missingCore.length > 0) {
    return (
      <Tip tone="warn">
        <strong>ETF 取不到 {missingCore.join("、")}</strong>
        {"，ETF 页面会没有价格或 K 线。ETF 行情和日线主要靠新浪，先看上面主机探测里 "}
        <code>vip.stock.finance.sina.com.cn</code> 和 <code>finance.sina.com.cn</code> 通不通。
        <br />
        <br />
        {expectedNote}
      </Tip>
    );
  }
  return (
    <Tip tone={missingExtra.length > 0 ? "warn" : "ok"}>
      <strong>ETF 的行情和 K 线都能从真实数据源取到。</strong>
      {missingExtra.length > 0 && (
        <>
          {` 但 ${missingExtra.join("、")} 取不到（来自天天基金 `}
          <code>fundf10.eastmoney.com</code>
          {"），分析 ETF 时基本面这一块会注明缺数据，价格和 K 线不受影响。"}
        </>
      )}
      <br />
      <br />
      {expectedNote}
    </Tip>
  );
}

/** 金价也单独一句：它的数据来自上金所官网和新浪外盘，和股票那几个站点无关。 */
function GoldAdvice({ result }: { result: Diagnosis }) {
  const real = result.providers.filter((p) => p.provider !== "mock");
  const ok = new Set<string>();
  let probed = false;
  for (const p of real) {
    for (const i of p.interfaces) {
      if (i.name.startsWith("金价")) probed = true;
      if (i.status === "正常") ok.add(i.name);
    }
  }
  if (!probed) return null; // 老版本后端没有金价探测

  const short = (name: string) => name.replace(/^金价·/, "");
  const sge = ["金价·上金所日线", "金价·上金所分时"].filter((n) => !ok.has(n));
  const london = ["金价·伦敦金行情", "金价·伦敦金日线"].filter((n) => !ok.has(n));

  if (sge.length === 0 && london.length === 0) {
    return (
      <Tip tone="ok">
        <strong>金价正常</strong>：上金所 Au99.99 和伦敦金都能取到。
      </Tip>
    );
  }
  return (
    <Tip tone="warn">
      {sge.length > 0 && (
        <>
          <strong>上金所金价取不到 {sge.map(short).join("、")}</strong>
          {"：数据来自上海黄金交易所官网，先看上面主机探测里 "}
          <code>www.sge.com.cn</code> 通不通。
          <br />
        </>
      )}
      {london.length > 0 && (
        <>
          <strong>伦敦金取不到 {london.map(short).join("、")}</strong>
          {"：数据来自新浪，报价看 "}
          <code>hq.sinajs.cn</code>，日线看 <code>stock2.finance.sina.com.cn</code>。
          <br />
        </>
      )}
      只影响「金价」页和首页的金价卡片，股票、ETF 不受影响。
    </Tip>
  );
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
