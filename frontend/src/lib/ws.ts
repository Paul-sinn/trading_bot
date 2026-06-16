// backend WebSocket 구독 헬퍼. 실시간 push(가격 티커 등)는 backend WS만 구독한다.
import type { TickerMessage } from "@/types";

const WS_BASE_URL =
  process.env.NEXT_PUBLIC_WS_BASE_URL ?? "ws://localhost:8000";

/**
 * `/ws/ticker` 구독. 메시지 수신 시 `onMessage` 호출, 구독 해제 함수를 반환한다.
 * SSR(window 없음) 환경에서는 no-op 해제 함수만 돌려준다.
 */
export function subscribeTicker(
  symbols: string[],
  onMessage: (msg: TickerMessage) => void,
): () => void {
  if (typeof window === "undefined") {
    return () => {};
  }
  const query = symbols.length ? `?symbols=${symbols.join(",")}` : "";
  const ws = new WebSocket(`${WS_BASE_URL}/ws/ticker${query}`);

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data as string) as TickerMessage;
      if (msg.type === "ticker") onMessage(msg);
    } catch (err) {
      console.error("WS 메시지 파싱 오류:", err);
    }
  };
  ws.onerror = (err) => console.error("WS 오류:", err);

  return () => ws.close();
}
