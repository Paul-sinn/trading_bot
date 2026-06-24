// 라이브 스캔 진단 패널 — 왜 매수/스킵인지 비전문가도 이해하게 보여준다(읽기 전용).
// CRITICAL: 진단 전용. 주문/승인 없음. 기본 화면은 사람 친화 설명, 기술 필드는 "고급 정보"로 접어둔다.
import { Card } from "@/components/ui/Card";
import type { ScanDiagnosticsView, SymbolDiagnostic } from "@/types";

const DECISION_LABEL: Record<string, string> = {
  BUY_CANDIDATE: "봇이 매수 후보로 선정",
  SKIPPED: "봇이 매수하지 않음",
  ERROR: "판단하지 못함(오류)",
};

function decisionTone(d: string): string {
  if (d === "BUY_CANDIDATE") return "text-emerald-400";
  if (d === "ERROR") return "text-red-400";
  return "text-neutral-400";
}

function tierLabel(tier: string | null): string {
  if (!tier) return "정책 없음";
  const labels: Record<string, string> = {
    "0": "Tier 0 · 시장 확인용",
    "1": "Tier 1 · 핵심 리더",
    "2": "Tier 2 · 모멘텀 코어",
    "3": "Tier 3 · 전력/인프라",
    "4A": "Tier 4A · 방산",
    "4B": "Tier 4B · 우주/고변동",
    "5": "Tier 5 · 고위험 관찰",
    "6": "Tier 6 · 크립토 베타",
  };
  return labels[tier] ?? `Tier ${tier}`;
}

function policyTone(d: SymbolDiagnostic): string {
  if (d.policy_tradable && d.approval_allowed) return "text-emerald-400";
  if (d.policy_status === "needs_review") return "text-red-400";
  if (d.policy_status === "watch") return "text-amber-400";
  return "text-neutral-400";
}

export function ScanDiagnosticsPanel({
  view,
}: {
  /** 최신 스캔 진단(읽기 전용). null이면 안내 표시. */
  view: ScanDiagnosticsView | null;
}) {
  const s = view?.summary ?? null;
  const symbols = view?.symbols ?? [];

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">라이브 스캔 진단</div>
        <span className="text-xs text-neutral-500">
          {s ? `${s.buy_candidates} 매수 / ${s.skipped} 스킵 / ${s.errors} 오류` : "—"}
        </span>
      </div>

      {/* 큰 한 줄 요약 — 비전문가용 */}
      <div className="rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-white">
        {s?.headline ?? "아직 스캔 기록이 없습니다. '거래 시작'(report_only)을 누르면 채워집니다."}
      </div>

      {s ? (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          <Kv k="시장 상태" v={s.market_condition.replace("시장 상태: ", "")} />
          <Kv k="스캔한 종목" v={String(s.total_scanned)} />
          <Kv k="대부분 스킵 이유" v={s.main_skip_reason ?? "—"} />
          {s.vix_warning ? <Kv k="주의" v={s.vix_warning} tone="text-amber-400" /> : null}
        </div>
      ) : null}

      {symbols.length > 0 ? <UniversePolicySummary symbols={symbols} /> : null}

      {/* 매수에 근접했던 종목 */}
      {s && s.top_closest.length > 0 ? (
        <div className="border-t border-neutral-800 pt-2">
          <div className="text-xs font-medium text-neutral-500">매수에 가장 근접했던 종목</div>
          <div className="mt-1 space-y-0.5">
            {s.top_closest.map((c) => (
              <div key={c.symbol} className="flex gap-x-2 text-xs">
                <span className="font-mono font-semibold text-white">{c.symbol}</span>
                <span className="text-amber-400">{c.signal_strength}</span>
                <span className="truncate text-neutral-400">{c.reason}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* 종목별 카드 */}
      <div className="space-y-2 border-t border-neutral-800 pt-2">
        {symbols.length === 0 ? (
          <div className="text-xs text-neutral-600">표시할 종목 진단이 없습니다.</div>
        ) : (
          symbols.map((d) => <SymbolCard key={d.symbol} d={d} />)
        )}
      </div>
    </Card>
  );
}

function UniversePolicySummary({ symbols }: { symbols: SymbolDiagnostic[] }) {
  const groups = [...symbols].sort((a, b) =>
    `${a.policy_tier ?? "Z"}-${a.symbol}`.localeCompare(`${b.policy_tier ?? "Z"}-${b.symbol}`),
  );
  const tradable = groups.filter((d) => d.policy_tradable && d.approval_allowed).length;

  return (
    <div className="border-t border-neutral-800 pt-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-neutral-500">라이브 유니버스 정책</div>
        <span className="text-xs text-neutral-500">{tradable}개 실전 매수 허용</span>
      </div>
      <div className="mt-1 grid gap-1 sm:grid-cols-2">
        {groups.map((d) => (
          <div key={`${d.symbol}-policy`} className="rounded-md border border-neutral-800 px-2 py-1.5">
            <div className="flex items-center gap-x-2">
              <span className="font-mono text-xs font-semibold text-white">{d.symbol}</span>
              <span className="text-[11px] text-neutral-500">{tierLabel(d.policy_tier)}</span>
              <span className={`ml-auto text-[11px] font-medium ${policyTone(d)}`}>{d.policy_label}</span>
            </div>
            <div className="mt-0.5 truncate text-[11px] text-neutral-400">{d.policy_reason}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SymbolCard({ d }: { d: SymbolDiagnostic }) {
  return (
    <div className="rounded-md border border-neutral-800 px-3 py-2">
      <div className="flex items-center gap-x-2">
        <span className="font-mono text-sm font-semibold text-white">{d.symbol}</span>
        <span className={`text-xs font-semibold ${decisionTone(d.final_decision)}`}>
          {DECISION_LABEL[d.final_decision] ?? d.final_decision}
        </span>
        <span className={`text-xs font-semibold ${policyTone(d)}`}>{d.policy_label}</span>
        <span className="ml-auto text-xs text-neutral-500">
          {d.price != null ? `$${d.price}` : "—"} · 신호 {d.signal_strength}
        </span>
      </div>
      <div className="mt-1 text-xs text-neutral-300">{d.human_reason}</div>
      <div className="mt-0.5 text-xs text-neutral-500">{tierLabel(d.policy_tier)} · {d.policy_reason}</div>

      {/* 고급 정보(접힘) — 기술 필드 분리 */}
      <details className="mt-1">
        <summary className="cursor-pointer text-[11px] text-neutral-500">고급 정보</summary>
        <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] text-neutral-400 sm:grid-cols-3">
          <span>추세: {d.trend_status}</span>
          <span>모멘텀: {d.momentum_status}</span>
          <span>눌림목: {d.pullback_status}</span>
          <span>거래량: {d.volume_status}</span>
          <span>레짐: {d.regime_status}</span>
          <span>데이터: {d.data_status}</span>
          <span>정책: {d.policy_status}/{d.policy_decision}</span>
          <span className="col-span-2 font-mono sm:col-span-3">
            technical: {d.technical_reason} · scan={d.scan_status} · regime={d.regime}/{d.regime_source}
          </span>
        </div>
      </details>
    </div>
  );
}

function Kv({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="space-y-0.5">
      <div className="text-neutral-500">{k}</div>
      <div className={`${tone ?? "text-neutral-200"} truncate`}>{v}</div>
    </div>
  );
}
