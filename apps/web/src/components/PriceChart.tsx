"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle } from "@/lib/analysis";

export interface ChartLevels {
  entry?: number | null;
  stop?: number | null;
  target?: number | null;
}

/**
 * The price chart.
 *
 * It draws the stored closed bars plus the active signal's entry, stop and
 * target. It does not draw predictions, and it does not fetch anything: the
 * caller owns the data so the chart and the analysis panel can never show
 * two different series.
 */
export function PriceChart({
  candles,
  levels,
  height = 360,
}: {
  candles: Candle[];
  levels?: ChartLevels;
  height?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!container.current) return;

    const instance = createChart(container.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#11141a" },
        textColor: "#8b93a3",
        fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1b2029" },
        horzLines: { color: "#1b2029" },
      },
      rightPriceScale: { borderColor: "#232833" },
      timeScale: { borderColor: "#232833", timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const candleSeries = instance.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    chart.current = instance;
    series.current = candleSeries;

    const observer = new ResizeObserver(() => {
      if (container.current) {
        instance.applyOptions({ width: container.current.clientWidth });
      }
    });
    observer.observe(container.current);

    return () => {
      observer.disconnect();
      instance.remove();
      chart.current = null;
      series.current = null;
      priceLines.current = [];
    };
  }, [height]);

  useEffect(() => {
    if (!series.current || candles.length === 0) return;

    const data: CandlestickData<UTCTimestamp>[] = candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    // Lightweight Charts requires strictly ascending, unique time values.
    data.sort((a, b) => (a.time as number) - (b.time as number));
    const unique: CandlestickData<UTCTimestamp>[] = [];
    for (const point of data) {
      if (unique.length === 0 || unique[unique.length - 1].time !== point.time) {
        unique.push(point);
      }
    }

    series.current.setData(unique);
    chart.current?.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    if (!series.current) return;
    for (const line of priceLines.current) {
      series.current.removePriceLine(line);
    }
    priceLines.current = [];

    const add = (
      price: number | null | undefined,
      color: string,
      title: string,
      style: LineStyle,
    ) => {
      if (price == null || !series.current) return;
      priceLines.current.push(
        series.current.createPriceLine({
          price,
          color,
          lineWidth: 1,
          lineStyle: style,
          axisLabelVisible: true,
          title,
        }),
      );
    };

    add(levels?.entry, "#3b82f6", "entry", LineStyle.Solid);
    add(levels?.stop, "#ef4444", "stop", LineStyle.Dashed);
    add(levels?.target, "#22c55e", "target", LineStyle.Dashed);
  }, [levels]);

  return (
    <div className="rounded-lg border border-border bg-panel overflow-hidden">
      <div ref={container} className="w-full" style={{ height }} />
    </div>
  );
}
