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

/** 带符号的价格变动额，例如 +12.30 / -0.45 */
export function fmtSigned(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(digits)}`;
}

/**
 * epoch 毫秒 -> 北京时间 HH:MM:SS。
 * 固定按上海时区展示：用户在国外用这个工具时，看到的也该是交易所时间。
 */
export function fmtClock(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  return new Date(ms).toLocaleTimeString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** 毫秒时长 -> "2 小时 13 分" / "13 分 20 秒"，给开盘倒计时用。 */
export function fmtCountdown(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms) || ms <= 0) return "即将开始";
  const totalMin = Math.floor(ms / 60_000);
  const days = Math.floor(totalMin / 1440);
  if (days >= 1) return `${days} 天 ${Math.floor((totalMin % 1440) / 60)} 小时`;
  const hours = Math.floor(totalMin / 60);
  if (hours >= 1) return `${hours} 小时 ${totalMin % 60} 分`;
  if (totalMin >= 1) return `${totalMin} 分 ${Math.floor((ms % 60_000) / 1000)} 秒`;
  return `${Math.max(Math.floor(ms / 1000), 1)} 秒`;
}

/** 交易时段对应的配色 class（背景 + 文字）。 */
export function phaseColor(phase: string | null | undefined): string {
  switch (phase) {
    case "连续竞价":
    case "集合竞价":
      return "bg-[var(--up-soft)] text-[var(--up)]";
    case "午间休市":
      return "bg-[var(--warn-soft)] text-[var(--warn)]";
    default:
      return "bg-[var(--surface-2)] text-[var(--muted)]";
  }
}
