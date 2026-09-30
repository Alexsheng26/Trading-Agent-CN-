"use client";

// ETF 概况：跟踪标的、规模、费率 + 前十大重仓股。
// ETF 没有财报和市盈率，这些才是它的"基本面"。
//
// 重仓股画成一列横条：单一系列、比的是大小，所以全部用同一个颜色
// （按大小换深浅是把"大小"又编码了一遍，还会被误读成类别）。
// 每行的数值都直接写出来 —— 这张列表本身就是表格视图，不靠悬停才能读数。

import { fmtMoney, fmtNum } from "@/lib/format";
import type { FundHolding, FundInfo } from "@/lib/types";

export function EtfPanel({
  info,
  loading,
  error,
}: {
  info: FundInfo | null;
  loading: boolean;
  error: string | null;
}) {
  const profile = info?.profile ?? null;
  const holdings = info?.holdings ?? [];

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-medium">ETF 概况</h2>
        {profile?.fund_type && (
          <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">
            {profile.fund_type}
          </span>
        )}
      </div>

      {loading && !info ? (
        <div className="mt-3 space-y-2" aria-hidden>
          <div className="h-4 w-2/3 animate-pulse rounded bg-[var(--surface-2)]" />
          <div className="h-4 w-1/2 animate-pulse rounded bg-[var(--surface-2)]" />
        </div>
      ) : error ? (
        <p className="mt-2 text-xs leading-relaxed text-[var(--warn)]">
          ETF 概况取不到：{error}
          <br />
          行情和 K 线不受影响。去「设置 → 数据源诊断」看「ETF 概况 / ETF 持仓」两行。
        </p>
      ) : (
        <>
          {profile && (
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5 text-xs md:grid-cols-3">
              <Item label="跟踪标的" value={profile.tracking_index || "—"} wide />
              <Item
                label="资产规模"
                value={fmtMoney(profile.asset_size)}
                hint={profile.asset_size_date ? `截至 ${profile.asset_size_date}` : undefined}
              />
              <Item
                label="份额规模"
                value={profile.share_size != null ? `${fmtMoney(profile.share_size)}份` : "—"}
              />
              <Item label="成立日期" value={profile.inception_date || "—"} />
              <Item
                label="管理费 / 托管费"
                value={`${fmtFee(profile.management_fee)} / ${fmtFee(profile.custody_fee)}`}
                hint="每年"
              />
              <Item label="基金管理人" value={profile.manager || "—"} />
            </dl>
          )}

          {holdings.length > 0 && <Holdings holdings={holdings} divided={profile !== null} />}

          {info && info.warnings.length > 0 && (
            <p className="mt-3 text-[11px] leading-relaxed text-[var(--warn)]">
              {info.warnings.join("；")}
            </p>
          )}
        </>
      )}
    </section>
  );
}

function Holdings({ holdings, divided }: { holdings: FundHolding[]; divided: boolean }) {
  const max = Math.max(...holdings.map((h) => h.weight_pct ?? 0), 0);
  const total = holdings.reduce((sum, h) => sum + (h.weight_pct ?? 0), 0);
  const period = holdings[0]?.period;

  return (
    <div className={divided ? "mt-4 border-t border-[var(--border)] pt-3" : "mt-3"}>
      <div className="flex items-baseline justify-between gap-3 text-[11px] text-[var(--muted)]">
        <span>
          前 {holdings.length} 大重仓股{period ? ` · ${period}` : ""}
        </span>
        <span className="tabular">合计占净值 {fmtNum(total)}%</span>
      </div>

      <ol className="mt-2 space-y-0.5" aria-label="前十大重仓股及占净值比例">
        {holdings.map((h) => {
          const w = h.weight_pct ?? 0;
          return (
            <li
              key={`${h.code}-${h.name}`}
              // 悬停提示是补充，不是唯一读数途径：数值已经写在行尾
              title={`${h.name}（${h.code}）占净值 ${fmtNum(h.weight_pct)}%`}
              className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)_3.25rem] items-center gap-2 rounded px-1 py-1 hover:bg-[var(--surface-2)]"
            >
              <span className="truncate text-xs">
                {h.name}
                <span className="tabular ml-1 text-[10px] text-[var(--muted)]">{h.code}</span>
              </span>
              {/* 从同一条基线长出来：左端方、右端 4px 圆角，6px 粗 */}
              <span className="block h-1.5">
                <span
                  className="block h-full rounded-r bg-[var(--accent)]"
                  style={{ width: max > 0 ? `${(w / max) * 100}%` : "0%" }}
                />
              </span>
              <span className="tabular text-right text-xs">{fmtNum(h.weight_pct)}%</span>
            </li>
          );
        })}
      </ol>

      <p className="mt-2 text-[10px] leading-relaxed text-[var(--muted)]">
        季报披露的持仓，距今可能已有一到三个月；前十大合计越高，受少数龙头股影响越大。
      </p>
    </div>
  );
}

function Item({
  label,
  value,
  hint,
  wide = false,
}: {
  label: string;
  value: string;
  hint?: string;
  wide?: boolean;
}) {
  return (
    <div className={wide ? "col-span-2 md:col-span-3" : ""}>
      <dt className="text-[11px] text-[var(--muted)]">{label}</dt>
      <dd className="mt-0.5">
        {value}
        {hint && <span className="ml-1 text-[10px] text-[var(--muted)]">{hint}</span>}
      </dd>
    </div>
  );
}

function fmtFee(value: number | null | undefined): string {
  return value == null ? "—" : `${fmtNum(value)}%`;
}
