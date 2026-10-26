"use client";

// 响应式外壳：
//   手机 (< md)  顶部标题栏 + 底部 Tab 导航（拇指可达）
//   桌面 (>= md) 左侧固定侧边栏

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SystemStatus } from "@/lib/types";

const NAV = [
  { href: "/", label: "自选", icon: StarIcon },
  { href: "/analyze", label: "分析", icon: SparkIcon },
  { href: "/gold", label: "金价", icon: GoldIcon },
  { href: "/runs", label: "历史", icon: HistoryIcon },
  { href: "/settings", label: "设置", icon: GearIcon },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [status, setStatus] = useState<SystemStatus | null>(null);

  useEffect(() => {
    const load = () => {
      api.status().then(setStatus).catch(() => setStatus(null));
    };

    // 分三段查：
    // 挂载时 —— 拿模型/数据源配置，用于侧边栏；
    // 4 秒后 —— 此时页面的首批数据请求已经完成，后端才知道实际命中了哪个源。
    //   只在挂载时查一次的话 last_sources 还是空的，降级警示条根本不会出现（竞态）；
    // 之后每 30 秒 —— 跟进运行期间发生的降级。
    load();
    const kick = setTimeout(load, 4000);
    const timer = setInterval(load, 30000);
    return () => {
      clearTimeout(kick);
      clearInterval(timer);
    };
  }, [pathname]);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <div className="min-h-dvh md:flex">
      {/* ---------- 桌面侧边栏 ---------- */}
      <aside className="hidden md:flex md:w-56 lg:w-64 md:flex-col md:border-r md:border-[var(--border)] md:bg-[var(--surface)] md:sticky md:top-0 md:h-dvh">
        <div className="px-5 py-5">
          <Link href="/" className="block">
            <div className="text-base font-semibold">A股投研助手</div>
            <div className="mt-0.5 text-xs text-[var(--muted)]">AI 多智能体分析</div>
          </Link>
        </div>
        <nav className="flex-1 px-2">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`mb-1 flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                isActive(href)
                  ? "bg-[var(--accent-soft)] text-[var(--accent)] font-medium"
                  : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
              }`}
            >
              <Icon className="h-5 w-5 shrink-0" />
              {label}
            </Link>
          ))}
        </nav>
        {status && <StubBadge status={status} className="mx-3 mb-4" />}
      </aside>

      {/* ---------- 主区域 ---------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 手机顶栏 */}
        <header className="sticky top-0 z-20 flex items-center justify-between border-b border-[var(--border)] bg-[var(--surface)]/95 px-4 py-3 backdrop-blur md:hidden">
          <Link href="/" className="text-[15px] font-semibold">
            A股投研助手
          </Link>
          {status?.llm.is_stub && (
            <span className="rounded bg-[var(--warn-soft)] px-2 py-0.5 text-[10px] text-[var(--warn)]">
              离线桩
            </span>
          )}
        </header>

        {/* 模拟数据警示。
            这是金融工具里最要紧的一条：模拟行情是程序生成的假数字，
            被误当成真实行情后果很严重，所以必须不可忽视地摆在内容上方，
            而不是藏在设置页里。 */}
        {status?.using_mock_data && (
          <div className="border-b border-[var(--warn)] bg-[var(--warn-soft)] px-4 py-2.5 md:px-6">
            <div className="flex items-start gap-2 text-[var(--warn)]">
              <svg viewBox="0 0 16 16" className="mt-[2px] h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                <path d="M8 5.5v4M8 11.5h.01" />
                <circle cx="8" cy="8" r="6.5" />
              </svg>
              <div className="min-w-0 text-xs leading-relaxed">
                <strong>当前显示的部分数据是模拟数据</strong>
                （真实数据源取数失败，已自动降级）。
                <span className="text-[var(--text)]">
                  {" "}
                  这些价格和指标都是程序生成的假数字，不要用于任何判断。
                </span>
                {/* 数据源明细是排查用的，手机上收起来 ——
                    核心警告要醒目，但不该占掉三分之一屏 */}
                {status.active_sources && (
                  <div className="mt-0.5 hidden text-[11px] opacity-80 sm:block">
                    实际数据源：
                    {Object.entries(status.active_sources)
                      .map(([k, v]) => `${k}=${v}`)
                      .join("、")}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1 pb-[calc(4.5rem+var(--safe-bottom))] md:pb-0">
          {children}
        </main>

        {/* 免责声明 —— 投资类工具必须常驻可见 */}
        <footer className="hidden border-t border-[var(--border)] px-6 py-4 text-xs leading-relaxed text-[var(--muted)] md:block">
          本工具输出由 AI 生成，仅供研究参考，<strong className="text-[var(--warn)]">不构成投资建议</strong>。
          证券投资有风险，请独立决策。
        </footer>
      </div>

      {/* ---------- 手机底部 Tab ---------- */}
      <nav className="fixed bottom-0 left-0 right-0 z-30 border-t border-[var(--border)] bg-[var(--surface)]/97 backdrop-blur md:hidden">
        <div
          className="flex"
          style={{ paddingBottom: "var(--safe-bottom)" }}
        >
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`flex flex-1 flex-col items-center gap-1 py-2.5 text-[11px] transition-colors ${
                isActive(href) ? "text-[var(--accent)]" : "text-[var(--muted)]"
              }`}
            >
              <Icon className="h-[22px] w-[22px]" />
              {label}
            </Link>
          ))}
        </div>
      </nav>
    </div>
  );
}

function StubBadge({ status, className = "" }: { status: SystemStatus; className?: string }) {
  if (!status.llm.is_stub) {
    return (
      <div className={`rounded-lg bg-[var(--surface-2)] p-3 text-[11px] leading-relaxed ${className}`}>
        <div className="text-[var(--muted)]">模型</div>
        <div className="mt-0.5 font-medium">{status.llm.model}</div>
        <div className="mt-1.5 text-[var(--muted)]">
          {/* 显示实际命中的源，而不是 healthy 列表 ——
              后者只说明依赖装好了，接口全挂时也是 true，会误导 */}
          数据源{" "}
          {Object.keys(status.active_sources ?? {}).length > 0
            ? [...new Set(Object.values(status.active_sources ?? {}))].join(" / ")
            : status.data_providers
                .filter((p) => p.healthy)
                .map((p) => p.name)
                .join(" / ") + "（待验证）"}
        </div>
      </div>
    );
  }
  return (
    <div className={`rounded-lg bg-[var(--warn-soft)] p-3 text-[11px] leading-relaxed text-[var(--warn)] ${className}`}>
      <strong>离线桩模式</strong>
      <div className="mt-1">
        当前用的是占位模型与模拟数据，结论无任何参考价值。请在 .env 配置
        ANTHROPIC_API_KEY 并把 LLM_PROVIDER 设为 anthropic。
      </div>
    </div>
  );
}

/* ---------------- 图标（内联 SVG，不引图标库） ---------------- */

function StarIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3z" />
    </svg>
  );
}

function SparkIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.8 2.8M14.9 14.9l2.8 2.8M17.7 6.3l-2.8 2.8M9.1 14.9l-2.8 2.8" />
      <circle cx="12" cy="12" r="2.6" />
    </svg>
  );
}

function GoldIcon({ className }: { className?: string }) {
  // 两块叠着的金条
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3.5 19.5h17l-2.4-5.5H5.9l-2.4 5.5z" />
      <path d="M7.4 14l2-5h5.2l2 5" />
      <path d="M12 3.5v1.6M8.4 5l.9 1.1M15.6 5l-.9 1.1" />
    </svg>
  );
}

function HistoryIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1M3.5 4.5V9h4.5" />
      <path d="M12 7.5V12l3.2 1.9" />
    </svg>
  );
}

function GearIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}
