"use client";

// K线图。用 lightweight-charts（TradingView 开源库，40KB，手机上性能好）。
// 注意：A股红涨绿跌，所以 upColor 是红、downColor 是绿。

import type { Time, UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Bar, FibAnalysis } from "@/lib/types";

/**
 * 分钟线（2 小时线）带 time：把北京时间直接当成 UTC 时间戳交给图表。
 * 图表按 UTC 显示，坐标轴上看到的就是北京时间 —— 和看图的人电脑设在哪个时区无关。
 * 日线、周线用日期字符串。
 */
function chartTime(bar: Bar): Time {
  if (!bar.time) return bar.date;
  const [day, clock = "00:00"] = bar.time.split("T");
  const [y, m, d] = day.split("-").map(Number);
  const [hh, mm] = clock.split(":").map(Number);
  return (Date.UTC(y, m - 1, d, hh, mm) / 1000) as UTCTimestamp;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 十字光标处的时间：分钟线 "10-09 11:30"，日线、周线 "2026-10-09" */
function formatTime(t: Time): string {
  if (typeof t === "number") {
    const d = new Date(t * 1000);
    return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  }
  if (typeof t === "string") return t;
  return `${t.year}-${pad(t.month)}-${pad(t.day)}`;
}

export function PriceChart({
  bars,
  fib,
  showFib = false,
  height = 260,
  decimals = 2,
  emptyText = "暂无 K 线数据",
}: {
  bars: Bar[];
  fib?: FibAnalysis | null;
  /** 是否在图上叠加斐波那契价位线 */
  showFib?: boolean;
  height?: number;
  /** 价格小数位：ETF 传 3，否则坐标轴和价位线会把 0.873 显示成 0.87 */
  decimals?: number;
  /** 没有 K 线时显示什么（加载中 / 取不到的原因） */
  emptyText?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || bars.length === 0) return;

    let disposed = false;
    let cleanup: (() => void) | undefined;

    // 动态 import：图表库只在需要 K 线的页面加载，首屏更快
    void (async () => {
      const { createChart, CandlestickSeries, HistogramSeries, ColorType } = await import(
        "lightweight-charts"
      );
      if (disposed || !containerRef.current) return;

      const intraday = bars.some((b) => b.time);
      const style = getComputedStyle(document.documentElement);
      const v = (name: string) => style.getPropertyValue(name).trim();
      const up = v("--up") || "#f0554d";
      const down = v("--down") || "#29b57e";

      const chart = createChart(containerRef.current, {
        height,
        // 固定成 zh-CN：坐标轴日期用中文格式，也避免跟随系统 locale
        // （某些环境的 locale 不合法，会让 Intl 抛 Invalid language tag）
        localization: { locale: "zh-CN", timeFormatter: formatTime },
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: v("--muted") || "#8b93a7",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: v("--border") || "#262d3d" },
          horzLines: { color: v("--border") || "#262d3d" },
        },
        rightPriceScale: { borderColor: v("--border") || "#262d3d" },
        // 分钟线要在坐标轴上显示时刻
        timeScale: {
          borderColor: v("--border") || "#262d3d",
          timeVisible: intraday,
          secondsVisible: false,
        },
        crosshair: { mode: 1 },
        // 手机上允许单指拖动看历史
        handleScroll: { vertTouchDrag: false },
      });

      const candles = chart.addSeries(CandlestickSeries, {
        priceFormat: { type: "price", precision: decimals, minMove: 10 ** -decimals },
        upColor: up,
        downColor: down,
        borderUpColor: up,
        borderDownColor: down,
        wickUpColor: up,
        wickDownColor: down,
      });
      candles.setData(
        bars.map((b) => ({
          time: chartTime(b),
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })),
      );

      const volume = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
      });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      volume.setData(
        bars.map((b) => ({
          time: chartTime(b),
          value: b.volume,
          color: b.close >= b.open ? `${up}55` : `${down}55`,
        })),
      );

      // 斐波那契价位线。
      // 刻意只画回撤位和摆动端点 —— 扩展位（如 261.8%）远在数据区间之外，
      // 画上去会把纵轴拉扁，K 线全挤成一条。扩展位在下方的面板里用文字给出。
      if (showFib && fib) {
        const accent = v("--accent") || "#5b8def";
        const muted = v("--muted") || "#8b93a7";

        // 标签策略：7 条线全画（提供视觉参照），但只给 4 条打价格轴徽标 ——
        // 两个摆动端点（在图的上下两端，位置远）+ 紧贴现价的支撑和阻力
        // （当下真正要用的两条）。
        // 全部打徽标的话，中间几条会和现价徽标、轴刻度糊成一片，手机上没法看；
        // 全部不打则失去标识，线就没意义了。lightweight-charts 的 title
        // 也依赖 axisLabelVisible，关掉徽标等于连线名一起没了。
        const labelled = new Set(
          [fib.nearest_support?.price, fib.nearest_resistance?.price].filter(
            (x): x is number => typeof x === "number",
          ),
        );

        for (const level of fib.levels) {
          if (level.kind === "extension") continue;
          const isSwing = level.kind === "swing";
          const isAdjacent = labelled.has(level.price);
          candles.createPriceLine({
            price: level.price,
            color: isSwing ? muted : accent,
            lineWidth: isAdjacent ? 2 : 1,
            lineStyle: isSwing ? 0 : 2, // 0=实线（端点），2=虚线（回撤位）
            axisLabelVisible: isSwing || isAdjacent,
            title: level.label,
          });
        }
      }

      chart.timeScale().fitContent();

      // 容器宽度变化时（转屏、侧边栏展开）跟着重排
      const observer = new ResizeObserver(([entry]) => {
        chart.applyOptions({ width: entry.contentRect.width });
      });
      observer.observe(containerRef.current);

      cleanup = () => {
        observer.disconnect();
        chart.remove();
      };
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [bars, height, fib, showFib, decimals]);

  if (bars.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--surface)] px-4 text-center text-xs leading-relaxed text-[var(--muted)]"
        style={{ height }}
      >
        {emptyText}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
      <div ref={containerRef} />
    </div>
  );
}
