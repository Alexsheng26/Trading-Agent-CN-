// 代码层面的品种判断。和后端 app/data/symbols.py 的 ETF_PREFIXES 保持一致 ——
// 前端需要在行情回来之前就知道"这是不是 ETF"（决定价格几位小数、要不要拉 ETF 概况）。
//
// 行情回来之后以后端给的 quote.asset_type / price_decimals 为准。

const ETF_PREFIXES: Record<string, readonly string[]> = {
  SH: ["51", "52", "56", "58"],
  SZ: ["159"],
};

/** 规范代码（600519.SH / 512480.SH）是不是场内 ETF。 */
export function isEtfSymbol(symbol: string | null | undefined): boolean {
  const [code, exchange] = (symbol ?? "").toUpperCase().split(".");
  if (!code || !exchange) return false;
  return (ETF_PREFIXES[exchange] ?? []).some((p) => code.startsWith(p));
}

/** 价格小数位：ETF 最小变动 0.001 元，股票 0.01 元。 */
export function priceDecimals(
  symbol: string | null | undefined,
  quote?: { price_decimals?: number } | null,
): number {
  return quote?.price_decimals ?? (isEtfSymbol(symbol) ? 3 : 2);
}
