"use client";

// 统计块。约定：标签（句首大写、不带冒号）+ 数值 + 可选的变化量。
//
// 一条容易搞错的排版规则：**大号独立数字不用等宽字体**。
// tabular-nums 会让每位数字都占 "0" 的宽度，大字号下看起来发散；
// 等宽只该用在需要纵向对齐的场合（表格列、坐标轴刻度）。

import { changeColor } from "@/lib/format";

export function StatTile({
  label,
  value,
  direction,
  delta,
  hint,
  size = "md",
}: {
  label: string;
  value: string;
  /**
   * 数值本身就是涨跌时传它，数值会按方向着色（A股红涨绿跌）。
   * 不要为此再传一个 delta —— 那会把同一个数字显示两遍。
   */
  direction?: number | null;
  /** 数值是绝对量、另有一个变化量要展示时才用 */
  delta?: { text: string; direction: number | null | undefined };
  hint?: string;
  size?: "md" | "lg";
}) {
  return (
    <div className="min-w-[7rem] shrink-0 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5 md:min-w-0">
      <div className="truncate text-[11px] text-[var(--muted)]">{label}</div>
      <div
        className={`mt-1 font-semibold leading-tight ${
          size === "lg" ? "text-xl" : "text-[15px]"
        } ${direction !== undefined ? changeColor(direction) : ""}`}
      >
        {value}
      </div>
      {delta && (
        <div className={`text-[11px] ${changeColor(delta.direction)}`}>{delta.text}</div>
      )}
      {hint && !delta && (
        <div className="truncate text-[11px] text-[var(--muted)]">{hint}</div>
      )}
    </div>
  );
}

/** 横向滚动的统计块组，手机上可滑动、桌面上自动换行铺满。 */
export function StatRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 md:mx-0 md:grid md:grid-cols-3 md:overflow-visible md:px-0 lg:grid-cols-6">
      {children}
    </div>
  );
}
