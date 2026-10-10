"use client";

// 首页：大盘 + 自选股。手机上是卡片列表，桌面上是表格。

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { SessionBadge, TickPrice } from "@/components/LivePrice";
import { StockSearch } from "@/components/StockSearch";
import { api } from "@/lib/api";
import { changeColor, fmtClock, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { isEtfSymbol, priceDecimals } from "@/lib/symbols";
import { useLiveIndexes, useLiveQuotes } from "@/lib/useLiveQuote";
import { codeOf } from "@/lib/useWatchlistCodes";
import type { Quote, StockBasic, WatchlistItem } from "@/lib/types";

export default function HomePage() {
  const router = useRouter();
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  // 自选股的名称/备注来自数据库，价格走实时轮询 —— 两者刷新节奏完全不同
  const symbols = useMemo(() => items.map((i) => i.symbol), [items]);
  const live = useLiveQuotes(symbols);
  const market = useLiveIndexes();
  const indexes = market.indexes;

  // 实时价格覆盖数据库里那份快照，取不到时退回快照，不让列表变空
  const rows = useMemo(
    () =>
      items.map((item) => ({
        ...item,
        quote: live.quotes[item.symbol] ?? item.quote ?? null,
      })),
    [items, live.quotes],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api.watchlist(true));
      setMessage(null);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 搜索结果旁的「+ 自选」。失败时抛出去，由搜索框在那一行下面显示原因
  const add = async (stock: StockBasic) => {
    const created = await api.addToWatchlist(stock.symbol);
    setItems((prev) => [...prev, created]);
  };
  const addedCodes = useMemo(() => new Set(items.map((i) => codeOf(i.symbol))), [items]);

  const remove = async (id: number) => {
    try {
      await api.removeFromWatchlist(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "删除失败");
    }
  };

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-4 md:px-6 md:py-6">
      {/* 大盘 */}
      <div className="mb-2 flex items-center gap-2">
        <SessionBadge session={market.session} polling={market.polling} />
        {/* 只有真的拿到过行情才显示时间戳。
            取数失败时还写一句"行情更新于 22:04"，比什么都不写更误导人。 */}
        {live.updatedAt !== null && Object.keys(live.quotes).length > 0 && (
          <span className="tabular text-[10px] text-[var(--muted)]">
            行情更新于 {fmtClock(live.updatedAt)}
          </span>
        )}
      </div>

      {/* 行情取不到时要说清原因。一排"—"却不给理由，用户只会以为工具坏了 */}
      {live.error && items.length > 0 && (
        <div className="mb-3 rounded-lg bg-[var(--warn-soft)] px-3 py-2 text-[11px] leading-relaxed text-[var(--warn)]">
          <strong>行情取不到</strong>：{live.error}
          <br />
          价格列显示为「—」是因为没拿到真实数据 —— 本工具不会用模拟数字填充。
          去「设置 → 数据源诊断」看是哪一步失败。
        </div>
      )}

      {indexes.length > 0 && (
        <div className="no-scrollbar -mx-4 mb-4 flex gap-2 overflow-x-auto px-4 md:mx-0 md:px-0">
          {indexes.map((idx) => (
            <div
              key={idx.code}
              className="min-w-[7.5rem] shrink-0 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
            >
              <div className="truncate text-[11px] text-[var(--muted)]">{idx.name}</div>
              <div className="tabular mt-0.5 text-sm font-medium">{fmtNum(idx.price)}</div>
              <div className={`tabular text-[11px] ${changeColor(idx.change_pct)}`}>
                {fmtPct(idx.change_pct)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 点结果 = 进个股页看看；想长期关注再点它右边的「+ 自选」 */}
      <StockSearch
        onPick={(s) => router.push(`/analyze?symbol=${encodeURIComponent(s.symbol)}`)}
        onAdd={add}
        isAdded={(symbol) => addedCodes.has(codeOf(symbol))}
      />

      {message && (
        <div className="mt-3 rounded-lg bg-[var(--warn-soft)] px-3 py-2 text-xs text-[var(--warn)]">
          {message}
        </div>
      )}

      <h2 className="mt-6 mb-2 text-sm font-medium text-[var(--muted)]">
        我的自选 {items.length > 0 && `(${items.length})`}
      </h2>

      {loading && items.length === 0 ? (
        <SkeletonList />
      ) : items.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          {/* 手机：卡片 */}
          <ul className="space-y-2 md:hidden">
            {rows.map((item) => (
              <li key={item.id}>
                <WatchCard item={item} onRemove={() => void remove(item.id)} />
              </li>
            ))}
          </ul>

          {/* 桌面：表格 */}
          <div className="hidden overflow-hidden rounded-xl border border-[var(--border)] md:block">
            <table className="w-full text-sm">
              <thead className="bg-[var(--surface-2)] text-[11px] text-[var(--muted)]">
                <tr>
                  <th className="px-4 py-2.5 text-left font-medium">名称 / 代码</th>
                  <th className="px-4 py-2.5 text-right font-medium">最新价</th>
                  <th className="px-4 py-2.5 text-right font-medium">涨跌幅</th>
                  <th className="px-4 py-2.5 text-right font-medium">成交额</th>
                  <th className="px-4 py-2.5 text-right font-medium">换手率</th>
                  <th className="px-4 py-2.5 text-right font-medium">PE(TTM)</th>
                  <th className="px-4 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                {rows.map((item) => (
                  <tr key={item.id} className="hover:bg-[var(--surface-2)]">
                    <td className="px-4 py-3">
                      <Link href={`/analyze?symbol=${item.symbol}`} className="block">
                        <span className="font-medium">{item.name || item.symbol}</span>
                        <span className="tabular ml-2 text-xs text-[var(--muted)]">
                          {item.symbol}
                        </span>
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <TickPrice
                        value={item.quote?.price}
                        digits={priceDecimals(item.symbol, item.quote)}
                      />
                    </td>
                    <td className={`tabular px-4 py-3 text-right ${changeColor(item.quote?.change_pct)}`}>
                      {fmtPct(item.quote?.change_pct)}
                    </td>
                    <td className="tabular px-4 py-3 text-right text-[var(--muted)]">
                      {fmtMoney(item.quote?.amount)}
                    </td>
                    <td className="tabular px-4 py-3 text-right text-[var(--muted)]">
                      {fmtPct(item.quote?.turnover_rate, false)}
                    </td>
                    <td className="tabular px-4 py-3 text-right text-[var(--muted)]">
                      {/* ETF 没有市盈率，直接标品种，别显示一个像是"取数失败"的横杠 */}
                      {isEtfSymbol(item.symbol) ? "ETF" : fmtNum(item.quote?.pe_ttm)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link
                        href={`/analyze?symbol=${item.symbol}`}
                        className="text-xs text-[var(--accent)] hover:underline"
                      >
                        分析
                      </Link>
                      <button
                        type="button"
                        onClick={() => void remove(item.id)}
                        className="ml-3 text-xs text-[var(--muted)] hover:text-[var(--up)]"
                      >
                        移除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function WatchCard({
  item,
  onRemove,
}: {
  item: WatchlistItem & { quote: Quote | null };
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3">
      <Link href={`/analyze?symbol=${item.symbol}`} className="min-w-0 flex-1">
        <div className="truncate text-[15px] font-medium">{item.name || item.symbol}</div>
        <div className="tabular text-xs text-[var(--muted)]">
          {item.symbol}
          {item.note ? ` · ${item.note}` : ""}
        </div>
      </Link>
      <div className="shrink-0 text-right">
        <div className="text-[15px] font-medium">
          <TickPrice value={item.quote?.price} digits={priceDecimals(item.symbol, item.quote)} />
        </div>
        <div className={`tabular text-xs ${changeColor(item.quote?.change_pct)}`}>
          {fmtPct(item.quote?.change_pct)}
        </div>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label="移出自选"
        className="shrink-0 px-1 text-[var(--muted)]"
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

function SkeletonList() {
  return (
    <ul className="space-y-2">
      {[0, 1, 2].map((i) => (
        <li
          key={i}
          className="h-[4.5rem] animate-pulse rounded-xl border border-[var(--border)] bg-[var(--surface)]"
        />
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <div className="rounded-xl border border-dashed border-[var(--border)] px-4 py-10 text-center">
      <p className="text-sm text-[var(--muted)]">自选股还是空的</p>
      <p className="mt-1 text-xs text-[var(--muted)]">
        在上面搜索，点结果右边的「+ 自选」加入第一只
      </p>
      <Link
        href="/analyze"
        className="mt-4 inline-block rounded-lg bg-[var(--accent)] px-4 py-2 text-sm text-white"
      >
        直接去分析一只股票
      </Link>
    </div>
  );
}
