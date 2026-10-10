"use client";

// "哪些代码已经在自选里"：搜索结果旁的「+ 自选」、个股页的加/移自选按钮都要用。
// 只要代码和 id（移出时要用），不拉行情 —— 首页那份带实时价的列表另有一套。
//
// 按 6 位代码比较，不按 600519.SH 这种完整写法：个股页的地址可能是用户手打的
// ?symbol=600519，不带后缀。A 股和场内 ETF 的 6 位代码在沪深京之间不重复。

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export function codeOf(symbol: string): string {
  return symbol.split(".")[0];
}

export function useWatchlistCodes() {
  // 6 位代码 -> 自选记录 id
  const [ids, setIds] = useState<ReadonlyMap<string, number>>(new Map());

  useEffect(() => {
    let alive = true;
    api
      .watchlist(false)
      .then((items) => {
        if (alive) setIds(new Map(items.map((i) => [codeOf(i.symbol), i.id])));
      })
      // 拿不到就当都没加：按钮显示「+ 自选」，真加的时候后端会说重复
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const has = useCallback((symbol: string) => ids.has(codeOf(symbol)), [ids]);

  const add = useCallback(async (symbol: string) => {
    const item = await api.addToWatchlist(symbol);
    setIds((prev) => new Map(prev).set(codeOf(item.symbol), item.id));
  }, []);

  const remove = useCallback(
    async (symbol: string) => {
      const id = ids.get(codeOf(symbol));
      if (id === undefined) return;
      await api.removeFromWatchlist(id);
      setIds((prev) => {
        const next = new Map(prev);
        next.delete(codeOf(symbol));
        return next;
      });
    },
    [ids],
  );

  return { has, add, remove };
}
