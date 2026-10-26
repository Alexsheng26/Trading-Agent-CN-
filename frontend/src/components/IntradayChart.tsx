"use client";

// 分时图：当前交易日每分钟一个价。以昨收为基准线，线在上面是红、在下面是绿（A股习惯）。
// 和 K 线图一样用 lightweight-charts，时间也一样"把北京时间当 UTC 交给图表"。

import type { Time, UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { PricePoint } from "@/lib/types";

/** "2026-10-26T10:00:00"（北京时间）-> 图表用的时间戳 */
function chartTime(iso: string): UTCTimestamp {
  const [day, clock = "00:00"] = iso.split("T");
  const [y, m, d] = day.split("-").map(Number);
  const [hh, mm] = clock.split(":").map(Number);
  return (Date.UTC(y, m - 1, d, hh, mm) / 1000) as UTCTimestamp;
}

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * 坐标轴和十字光标都只显示时刻。
 * 夜盘跨了日期（周一的夜盘是上周五晚上），显示日期反而让人看不懂。
 */
function clock(t: Time): string {
  if (typeof t !== "number") return "";
  const d = new Date(t * 1000);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

export function IntradayChart({
  points,
  prevClose,
  height = 260,
  decimals = 2,
  emptyText = "暂无分时数据",
}: {
  points: PricePoint[];
  /** 昨收。有就画基准线、按它分红绿；没有就画一条普通的线 */
  prevClose?: number | null;
  height?: number;
  decimals?: number;
  emptyText?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || points.length === 0) return;

    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      const { createChart, BaselineSeries, LineSeries, ColorType } = await import(
        "lightweight-charts"
      );
      if (disposed || !containerRef.current) return;

      const style = getComputedStyle(document.documentElement);
      const v = (name: string) => style.getPropertyValue(name).trim();
      const up = v("--up") || "#f0554d";
      const down = v("--down") || "#29b57e";
      const muted = v("--muted") || "#8b93a7";
      const border = v("--border") || "#262d3d";

      const chart = createChart(containerRef.current, {
        height,
        localization: { locale: "zh-CN", timeFormatter: clock },
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: muted,
          fontSize: 11,
        },
        grid: { vertLines: { color: border }, horzLines: { color: border } },
        rightPriceScale: { borderColor: border },
        timeScale: {
          borderColor: border,
          timeVisible: true,
          secondsVisible: false,
          tickMarkFormatter: clock,
        },
        crosshair: { mode: 1 },
        handleScroll: { vertTouchDrag: false },
      });

      const priceFormat = { type: "price" as const, precision: decimals, minMove: 10 ** -decimals };
      const data = points.map((p) => ({ time: chartTime(p.time), value: p.price }));

      if (prevClose != null) {
        const series = chart.addSeries(BaselineSeries, {
          priceFormat,
          baseValue: { type: "price", price: prevClose },
          lineWidth: 2,
          topLineColor: up,
          topFillColor1: `${up}40`,
          topFillColor2: `${up}08`,
          bottomLineColor: down,
          bottomFillColor1: `${down}08`,
          bottomFillColor2: `${down}40`,
        });
        series.setData(data);
        series.createPriceLine({
          price: prevClose,
          color: muted,
          lineWidth: 1,
          lineStyle: 2, // 虚线
          axisLabelVisible: true,
          title: "昨收",
        });
      } else {
        const series = chart.addSeries(LineSeries, {
          priceFormat,
          lineWidth: 2,
          color: v("--accent") || "#5b8def",
        });
        series.setData(data);
      }

      chart.timeScale().fitContent();

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
  }, [points, prevClose, height, decimals]);

  if (points.length === 0) {
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
