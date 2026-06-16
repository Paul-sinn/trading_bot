"use client";

import { useEffect, useState } from "react";
import { subscribeTicker } from "@/lib/ws";
import { mockTicker } from "@/lib/mock";
import type { TickerQuote } from "@/types";
import { formatUsd } from "@/lib/utils";

// 실시간 가격 티커. /ws/ticker 구독(1초 갱신). 백엔드 미가동 시 mock으로 graceful fallback.
export function LiveTicker() {
  // 초기값은 항상 mock — WS 미연결/실패 시에도 비지 않게.
  const [quotes, setQuotes] = useState<Record<string, TickerQuote>>(
    mockTicker.data,
  );

  useEffect(() => {
    const symbols = Object.keys(mockTicker.data);
    // WS 생성/연결 실패가 페이지를 크래시시키지 않도록 방어한다.
    let unsubscribe: () => void = () => {};
    try {
      unsubscribe = subscribeTicker(symbols, (msg) => setQuotes(msg.data));
    } catch (err) {
      console.error("티커 구독 실패 — mock 유지:", err);
    }
    return () => unsubscribe();
  }, []);

  const symbols = Object.keys(quotes);

  return (
    <div className="divide-y divide-neutral-800">
      {symbols.map((symbol) => (
        <div
          key={symbol}
          className="flex items-center justify-between py-2 text-sm"
        >
          <span className="font-medium text-white">{symbol}</span>
          <span className="tabular-nums text-neutral-300">
            {formatUsd(quotes[symbol].price)}
          </span>
        </div>
      ))}
    </div>
  );
}
