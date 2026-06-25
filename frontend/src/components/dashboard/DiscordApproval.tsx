// Discord 승인 게이트 패널 — 실주문(매수/매도) 전 사람 승인이 필요함을 보여준다(읽기 전용).
// CRITICAL: 승인은 리스크 게이트를 우회하지 않는다. 이 패널은 jsonl을 읽기만 하며 어떤 주문도 내지 않는다.
// pending 승인 / 최근 결정 / 만료 상태 / approve·reject 명령 예시를 표시한다.
import { Card } from "@/components/ui/Card";
import type { ApprovalView } from "@/types";

const STATUS_TONE: Record<string, string> = {
  PENDING: "text-amber-400",
  APPROVED: "text-emerald-400",
  REJECTED: "text-red-400",
  EXPIRED: "text-neutral-500",
  CANCELLED: "text-neutral-500",
};

export function DiscordApprovalPanel({
  approvals,
  latest,
}: {
  /** 승인 요청 목록(읽기 전용). */
  approvals: ApprovalView[];
  /** 가장 최근 승인 요청(없으면 null). */
  latest: ApprovalView | null;
}) {
  const pending = approvals.filter((a) => a.status === "PENDING");

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">Discord 승인 게이트</div>
        <span className="text-xs font-semibold text-amber-400">
          {pending.length > 0 ? `PENDING ${pending.length}` : "대기 없음"}
        </span>
      </div>

      <div className="rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs text-amber-400">
        Discord approval is required before any real order. Approval does not
        bypass risk gates.
      </div>

      <div className="border-t border-neutral-800 pt-2">
        <div className="text-xs font-medium text-neutral-500">대기 중 승인</div>
        {pending.length === 0 ? (
          <div className="text-xs text-neutral-600">없음</div>
        ) : (
          <div className="mt-1 space-y-1">
            {pending.map((a) => (
              <div key={a.approval_id} className="rounded border border-neutral-800 px-2 py-1 text-xs">
                <div className="flex gap-x-2">
                  <span className="font-mono font-semibold text-white">{a.symbol}</span>
                  <span className="text-neutral-400">
                    {a.type} · {a.order_type} · {a.notional != null ? `$${a.notional}` : "—"}
                  </span>
                  <span className={a.expired ? "text-neutral-500" : "text-amber-400"}>
                    {a.expired ? "만료" : "만료 전"}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-neutral-500">
                  거래일 {a.trading_date ?? "—"} · 신호 {a.intent_generated_at ?? "—"}
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-neutral-500">{a.approve_command}</div>
                <div className="font-mono text-[11px] text-neutral-500">{a.reject_command}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-neutral-800 pt-2 text-sm sm:grid-cols-3">
        <Item label="최근 요청" value={latest ? `${latest.type} ${latest.symbol}` : "—"} />
        <Item
          label="최근 상태"
          value={latest?.status ?? "—"}
          tone={latest ? STATUS_TONE[latest.status] : undefined}
        />
        <Item label="만료 여부" value={latest ? (latest.expired ? "만료됨" : "유효") : "—"} />
        <Item label="결정자" value={latest?.decided_by ?? "—"} />
        <Item label="최근 결정" value={latest?.decision ?? "—"} />
        <Item label="거래일" value={latest?.trading_date ?? "—"} />
        <Item label="만료시각" value={latest?.expires_at ?? "—"} mono />
      </div>
    </Card>
  );
}

function Item({
  label,
  value,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  tone?: string;
  mono?: boolean;
}) {
  return (
    <div className="space-y-0.5">
      <div className="text-xs font-medium text-neutral-500">{label}</div>
      <div
        className={`${tone ?? "text-white"} ${mono ? "font-mono text-xs" : "text-sm font-semibold"} truncate tabular-nums`}
      >
        {value}
      </div>
    </div>
  );
}
