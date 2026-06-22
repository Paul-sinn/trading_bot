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
import type {
  LiveCandidate,
  LiveScanEvent,
  LiveSessionState,
  OrderIntent,
} from "@/types";

interface LiveControlsProps {
  /** 서버에서 받은 초기 상태(읽기 전용). 실패 시 호출부가 mock(정지) 폴백. */
  initialStatus: LiveSessionState;
  /** 서버에서 받은 초기 스캔 이벤트(읽기 전용). 스캔 오류 표시에 사용. */
  initialScanEvents?: LiveScanEvent[];
  /** 서버에서 받은 초기 BUY 후보 + mock LLM 리뷰(읽기 전용). */
  initialCandidates?: LiveCandidate[];
  /** 서버에서 받은 초기 dry-run OrderIntent(읽기 전용). */
  initialOrderIntents?: OrderIntent[];
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
  initialCandidates = [],
  initialOrderIntents = [],
}: LiveControlsProps) {
  const [status, setStatus] = useState<LiveSessionState>(initialStatus);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const scanErrors = initialScanEvents.filter(
    (e) => e.scan_status === "ERROR",
  );
  // 상태의 latest_*가 비어 있으면 서버 props로 폴백(읽기 전용).
  const candidates =
    status.latest_candidates.length > 0
      ? status.latest_candidates
      : initialCandidates;
  const orderIntents =
    status.latest_order_intents.length > 0
      ? status.latest_order_intents
      : initialOrderIntents;

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

      {/* Mock LLM 의사결정 파이프라인(무비용 dry-run) */}
      <div className="space-y-2 border-t border-neutral-800 pt-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-amber-400">
            Mock LLM only — no paid API, no real orders
          </span>
          <span className="text-xs tabular-nums text-neutral-500">
            AI calls today: {status.ai_calls_today} · AI cost = $
            {status.ai_cost_estimate_today.toFixed(2)}
          </span>
        </div>

        {candidates.length === 0 ? (
          <div className="text-xs text-neutral-600">
            BUY 후보 없음 (스캔 대기)
          </div>
        ) : (
          <div className="space-y-1">
            {candidates.slice(-5).map((c) => {
              const intent = orderIntents.find(
                (o) => o.scan_event_key === c.scan_event_key,
              );
              return (
                <div
                  key={c.key}
                  className="flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-md bg-neutral-900 px-3 py-1.5 text-xs"
                >
                  <span className="font-mono font-semibold text-white">
                    {c.symbol}
                  </span>
                  <span className={reviewToneClass(c.review?.decision)}>
                    {c.review?.decision ?? c.block_reason ?? c.status}
                    {c.review
                      ? ` (${(c.review.confidence * 100).toFixed(0)}%)`
                      : ""}
                  </span>
                  <span className="text-neutral-500">
                    gate: {intent?.execution_gate_status ?? c.status}
                  </span>
                  {intent?.execution_gate_status === "accepted_dry_run" && (
                    <span className="tabular-nums text-neutral-400">
                      ${intent.planned_limit_price?.toFixed(2)} ×{" "}
                      {intent.planned_quantity?.toFixed(3)} = $
                      {intent.planned_notional_usd?.toFixed(0)}
                    </span>
                  )}
                  <span className="tabular-nums text-neutral-600">
                    orders={intent?.real_orders_placed ?? 0}
                  </span>
                </div>
              );
            })}
          </div>
        )}
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

function reviewToneClass(decision?: string): string {
  if (decision === "approve") return "font-semibold text-[#22c55e]";
  if (decision === "veto") return "font-semibold text-[#ef4444]";
  if (decision === "needs_review") return "font-semibold text-amber-400";
  return "font-semibold text-neutral-400";
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
