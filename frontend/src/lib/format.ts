// 数字/涨跌的展示格式。A股习惯：红涨绿跌（和欧美相反）。

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(value: number | null | undefined, withSign = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = withSign && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

/** 元 -> 亿/万，A股看盘习惯。 */
export function fmtMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)} 万亿`;
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`;
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万`;
  return value.toFixed(0);
}

/**
 * 涨跌对应的配色 class。
 * A股是红涨绿跌 —— 用欧美的绿涨红跌会让用户完全看反。
 */
export function changeColor(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) {
    return "text-[var(--flat)]";
  }
  return value > 0 ? "text-[var(--up)]" : "text-[var(--down)]";
}

export function signalColor(signal: string | null | undefined): string {
  switch (signal) {
    case "看多":
      return "text-[var(--up)] bg-[var(--up-soft)]";
    case "看空":
      return "text-[var(--down)] bg-[var(--down-soft)]";
    default:
      return "text-[var(--muted)] bg-[var(--surface-2)]";
  }
}

export function actionColor(action: string | null | undefined): string {
  switch (action) {
    case "买入":
    case "加仓":
      return "text-[var(--up)] bg-[var(--up-soft)] border-[var(--up)]";
    case "卖出":
    case "减仓":
      return "text-[var(--down)] bg-[var(--down-soft)] border-[var(--down)]";
    case "持有":
      return "text-[var(--accent)] bg-[var(--accent-soft)] border-[var(--accent)]";
    default:
      return "text-[var(--muted)] bg-[var(--surface-2)] border-[var(--border)]";
  }
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const diff = Date.now() - d;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return fmtTime(iso);
}
