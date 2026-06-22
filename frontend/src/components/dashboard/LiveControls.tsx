"use client";

// 라이브 트레이딩 세션 제어 + 상태 표시.
// CRITICAL: 매매 시작은 "거래 시작" 버튼(startLive) 클릭으로만 일어난다. 마운트/새로고침은
// 읽기 전용 getLiveStatus만 호출하며 절대 매매를 시작하거나 주문을 내지 않는다. backend가
// Robinhood MCP 어댑터를 통하고, 미연동 시 NOT_READY_NO_MCP를 표시한다(실주문 경로 없음).
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import {
  emergencyHalt,
  startLive,
  stopLive,
} from "@/lib/api";
import type { LiveScanEvent, LiveSessionState } from "@/types";

interface LiveControlsProps {
  /** 서버에서 받은 초기 상태(읽기 전용). 실패 시 호출부가 mock(정지) 폴백. */
  initialStatus: LiveSessionState;
  /** 서버에서 받은 초기 스캔 이벤트(읽기 전용). 스캔 오류 표시에 사용. */
  initialScanEvents?: LiveScanEvent[];
}

const STATUS_LABEL: Record<string, string> = {
  OK: "OK",
  NOT_READY_NO_MCP: "브로커 미연결 (Robinhood MCP 미연동)",
  BLOCKED_LIVE_DISABLED: "라이브 비활성 (LIVE_TRADING_ENABLED=false)",
  BLOCKED_EMERGENCY_HALT: "비상 정지 상태",
  BLOCKED_INVALID_MODE: "잘못된 모드",
};

export function LiveControls({
  initialStatus,
  initialScanEvents = [],
}: LiveControlsProps) {
  const [status, setStatus] = useState<LiveSessionState>(initialStatus);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const scanErrors = initialScanEvents.filter(
    (e) => e.scan_status === "ERROR",
  );

  async function onStart() {
    setBusy(true);
    setMessage(null);
    const res = await startLive("report_only");
    if (res) {
      setStatus(res.state);
      setMessage(STATUS_LABEL[res.status] ?? res.status);
    } else {
      setMessage("요청 실패 (백엔드 미연결)");
    }
    setBusy(false);
  }

  async function onStop() {
    setBusy(true);
    setMessage(null);
    const res = await stopLive("manual");
    if (res) {
      setStatus(res.state);
      setMessage("정지됨 — 신규 주문 차단");
    } else {
      setMessage("요청 실패 (백엔드 미연결)");
    }
    setBusy(false);
  }

  async function onHalt() {
    setBusy(true);
    setMessage(null);
    const res = await emergencyHalt();
    if (res) {
      setStatus(res.state);
      setMessage("비상 정지 — 신규 주문 차단");
    } else {
      setMessage("요청 실패 (백엔드 미연결)");
    }
    setBusy(false);
  }

  return (
    <div className="space-y-4">
      {/* report_only 모니터링 라벨 — 실주문 없음 명시 */}
      <div className="flex items-center justify-between rounded-md bg-neutral-900 px-3 py-2">
        <span className="text-xs font-medium text-amber-400">
          report_only monitoring — no real orders
        </span>
        <span className="text-xs tabular-nums text-neutral-500">
          real_orders_placed = {status.real_orders_placed}
        </span>
      </div>

      {/* 상태 요약 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <StatusItem
          label="자동화"
          value={status.automation_running ? "가동중" : "정지"}
          tone={status.automation_running ? "good" : "muted"}
        />
        <StatusItem
          label="브로커 연결"
          value={status.broker_connected ? "연결됨" : "미연결"}
          tone={status.broker_connected ? "good" : "warn"}
        />
        <StatusItem
          label="비상 정지"
          value={status.emergency_halt ? "ON" : "off"}
          tone={status.emergency_halt ? "bad" : "muted"}
        />
        <StatusItem label="세션 ID" value={status.session_id ?? "—"} mono />
        <StatusItem
          label="일일 주문 수"
          value={String(status.daily_order_count)}
        />
        <StatusItem
          label="실주문 수"
          value={String(status.real_orders_placed)}
          tone="muted"
        />
      </div>

      {/* 시장데이터 + 라이브 스캔 상태 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-neutral-800 pt-3 text-sm sm:grid-cols-3">
        <StatusItem label="시장데이터" value={status.market_data_provider || "—"} />
        <StatusItem
          label="데이터 상태"
          value={status.market_data_status || "—"}
          tone={status.market_data_status === "available" ? "good" : "warn"}
        />
        <StatusItem
          label="라이브 스캔"
          value={status.live_scan_running ? "가동중" : "정지"}
          tone={status.live_scan_running ? "good" : "muted"}
        />
        <StatusItem
          label="마지막 스캔"
          value={fmtScanTime(status.last_scan_at)}
          mono
        />
        <StatusItem
          label="스캔 이벤트 수"
          value={String(status.last_scan_event_count)}
        />
        <StatusItem
          label="BUY 후보"
          value={
            status.latest_buy_candidates.length
              ? status.latest_buy_candidates.join(", ")
              : "—"
          }
          tone={status.latest_buy_candidates.length ? "good" : "muted"}
        />
      </div>

      {/* 제어 버튼 */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="buy"
          onClick={onStart}
          disabled={busy || status.automation_running}
        >
          거래 시작
        </Button>
        <Button
          variant="primary"
          onClick={onStop}
          disabled={busy || !status.automation_running}
        >
          거래 정지
        </Button>
        <Button variant="danger" onClick={onHalt} disabled={busy}>
          비상 정지
        </Button>
      </div>

      {scanErrors.length > 0 && (
        <div className="space-y-1 rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2">
          <div className="text-xs font-medium text-[#ef4444]">
            최근 스캔 오류 ({scanErrors.length})
          </div>
          <div className="text-xs tabular-nums text-neutral-400">
            {scanErrors
              .slice(-3)
              .map((e) => `${e.symbol}: ${e.reason}`)
              .join(" · ")}
          </div>
        </div>
      )}

      {message && (
        <div className="text-xs tabular-nums text-neutral-400">{message}</div>
      )}
    </div>
  );
}

function fmtScanTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString();
}

function StatusItem({
  label,
  value,
  tone = "default",
  mono = false,
}: {
  label: string;
  value: string;
  tone?: "default" | "good" | "warn" | "bad" | "muted";
  mono?: boolean;
}) {
  const toneClass = {
    default: "text-white",
    good: "text-[#22c55e]",
    warn: "text-amber-400",
    bad: "text-[#ef4444]",
    muted: "text-neutral-400",
  }[tone];
  return (
    <div className="space-y-0.5">
      <div className="text-xs font-medium text-neutral-500">{label}</div>
      <div
        className={`${toneClass} ${mono ? "font-mono text-xs" : "text-sm font-semibold"} truncate`}
      >
        {value}
      </div>
    </div>
  );
}
