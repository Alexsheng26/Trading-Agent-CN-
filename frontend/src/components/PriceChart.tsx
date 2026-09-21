"use client";

// K线图。用 lightweight-charts（TradingView 开源库，40KB，手机上性能好）。
// 注意：A股红涨绿跌，所以 upColor 是红、downColor 是绿。

import { useEffect, useRef } from "react";
import type { Bar } from "@/lib/types";

export function PriceChart({ bars, height = 260 }: { bars: Bar[]; height?: number }) {
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

      const style = getComputedStyle(document.documentElement);
      const v = (name: string) => style.getPropertyValue(name).trim();
      const up = v("--up") || "#f0554d";
      const down = v("--down") || "#29b57e";

      const chart = createChart(containerRef.current, {
        height,
        // 固定成 zh-CN：坐标轴日期用中文格式，也避免跟随系统 locale
        // （某些环境的 locale 不合法，会让 Intl 抛 Invalid language tag）
        localization: { locale: "zh-CN" },
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
        timeScale: { borderColor: v("--border") || "#262d3d", timeVisible: false },
        crosshair: { mode: 1 },
        // 手机上允许单指拖动看历史
        handleScroll: { vertTouchDrag: false },
      });

      const candles = chart.addSeries(CandlestickSeries, {
        upColor: up,
        downColor: down,
        borderUpColor: up,
        borderDownColor: down,
        wickUpColor: up,
        wickDownColor: down,
      });
      candles.setData(
        bars.map((b) => ({
          time: b.date,
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
          time: b.date,
          value: b.volume,
          color: b.close >= b.open ? `${up}55` : `${down}55`,
        })),
      );

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
  }, [bars, height]);

  if (bars.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--muted)]"
        style={{ height }}
      >
        暂无 K 线数据
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
      <div ref={containerRef} />
    </div>
  );
}
