// ⑤ 목표 & 리스크 — "AI 분석하기" 패널.
// 목표기간·모드를 정해 backend POST /api/goal-plan(step 2)으로 역산 세팅 + AI 근거 + 실현가능성을
// 받아 검토용으로 표시하고, "적용"을 눌러야 반영한다(검토 후 적용 — 적용 전 활성 세팅 불변).
// CRITICAL(CLAUDE.md/ADR-001): 프론트는 Claude/거래소를 직접 호출하지 않고 backend REST만 부른다.
// 세팅 수치는 backend(또는 mock) 응답을 표시만 한다 — 프론트에서 재계산/하드캡 우회 금지(ADR-003/005).
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { applyGoalPlan, createGoalPlan } from "@/lib/api";
import { mockGoalPlan } from "@/lib/mock";
import type { Feasibility, GoalPlan, PlanMode } from "@/types";

const MODES: { value: PlanMode; label: string }[] = [
  { value: "safe", label: "안전 한도 내 (추천)" },
  { value: "aggressive", label: "목표 우선(공격적)" },
];

// 실현가능성 → 라벨 + 시맨틱 색상(녹/주/적). UI_GUIDE 데이터 색상 팔레트만 사용.
const FEASIBILITY_META: Record<Feasibility, { label: string; color: string }> = {
  realistic: { label: "현실적", color: "#22c55e" },
  ambitious: { label: "도전적", color: "#f59e0b" },
  unrealistic: { label: "비현실적", color: "#ef4444" },
};

/** 분수(0.05) → "5.0%" 표기. backend 단위(분수)와 일치. */
function pct(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}

function FeasibilityBadge({ feasibility }: { feasibility: Feasibility }) {
  const meta = FEASIBILITY_META[feasibility];
  return (
    <span
      data-testid="feasibility-badge"
      className="inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium"
      style={{ color: meta.color, borderColor: meta.color }}
    >
      {meta.label}
    </span>
  );
}

function SettingRow({
  testId,
  label,
  value,
}: {
  testId: string;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-sm text-neutral-400">{label}</span>
      <span
        data-testid={testId}
        className="text-sm tabular-nums text-white"
      >
        {value}
      </span>
    </div>
  );
}

function ResultPanel({ plan }: { plan: GoalPlan }) {
  const s = plan.settings;
  return (
    <div
      data-testid="ai-result-panel"
      className="space-y-4 rounded-lg border border-neutral-800 bg-[#1a1a1a] p-4"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-neutral-400">역산된 세팅</h3>
        <FeasibilityBadge feasibility={plan.feasibility} />
      </div>

      <div className="space-y-2">
        <SettingRow
          testId="setting-appetite"
          label="투자성향(appetite)"
          value={s.appetite.toFixed(2)}
        />
        <SettingRow
          testId="setting-max-risk"
          label="최대 리스크"
          value={pct(s.risk_limits.max_risk_pct)}
        />
        <SettingRow
          testId="setting-drawdown"
          label="드로우다운 한도"
          value={pct(s.risk_limits.max_drawdown_pct)}
        />
        <SettingRow
          testId="setting-max-position"
          label="최대 포지션 크기"
          value={pct(s.risk_limits.max_position_pct)}
        />
        <SettingRow
          testId="setting-stop"
          label="스탑로스 ATR 배수"
          value={`${s.stop_loss_atr_multiplier.toFixed(2)}×`}
        />
        <SettingRow
          testId="setting-monthly-return"
          label="필요 월 수익률"
          value={pct(plan.required_monthly_return)}
        />
      </div>

      <div className="space-y-1">
        <span className="text-sm font-medium text-neutral-400">AI 근거</span>
        <p
          data-testid="ai-rationale"
          className="text-sm leading-relaxed text-neutral-300"
        >
          {plan.rationale}
        </p>
      </div>
    </div>
  );
}

interface AiAnalysisPanelProps {
  /** 목표금액(현재 mock). current_equity는 생략 → backend 포트폴리오로 보완. */
  targetAmount: number;
}

export function AiAnalysisPanel({ targetAmount }: AiAnalysisPanelProps) {
  const [months, setMonths] = useState("12");
  const [mode, setMode] = useState<PlanMode>("safe");
  const [plan, setPlan] = useState<GoalPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [applied, setApplied] = useState(false);

  const inputClass =
    "w-full rounded-lg border border-neutral-800 bg-[#1a1a1a] px-4 py-3 text-sm tabular-nums text-white";

  function buildRequest() {
    return {
      target_amount: targetAmount,
      months: Number(months) || 0,
      mode,
    };
  }

  async function handleAnalyze() {
    setLoading(true);
    setApplied(false);
    // 백엔드 미가동/실패 시 mock 계획으로 graceful fallback — 크래시 금지.
    const result = await createGoalPlan(buildRequest());
    setPlan(result ?? mockGoalPlan);
    setLoading(false);
  }

  async function handleApply() {
    if (!plan) return;
    // 검토 후 적용: backend에 영속화(없으면 로컬 표시만). 활성 세팅 변경은 backend 권위.
    await applyGoalPlan(buildRequest());
    setApplied(true);
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <label
          htmlFor="goal-months"
          className="block text-sm font-medium text-neutral-400"
        >
          목표기간 (개월)
        </label>
        <input
          id="goal-months"
          data-testid="input-months"
          type="number"
          inputMode="numeric"
          min={1}
          value={months}
          onChange={(e) => setMonths(e.target.value)}
          className={inputClass}
        />
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-neutral-400">모드</legend>
        <div className="flex flex-col gap-2">
          {MODES.map((m) => (
            <label
              key={m.value}
              className="flex items-center gap-2 text-sm text-neutral-300"
            >
              <input
                type="radio"
                name="plan-mode"
                value={m.value}
                checked={mode === m.value}
                onChange={() => setMode(m.value)}
                className="accent-white"
              />
              {m.label}
            </label>
          ))}
        </div>
      </fieldset>

      <Button
        data-testid="analyze-btn"
        onClick={handleAnalyze}
        disabled={loading}
      >
        {loading ? "분석 중…" : "AI 분석하기"}
      </Button>

      {plan && (
        <div className="space-y-4">
          <ResultPanel plan={plan} />
          <div className="flex items-center gap-3">
            <Button
              data-testid="apply-btn"
              variant="primary"
              onClick={handleApply}
            >
              적용
            </Button>
            {applied && (
              <span className="text-sm text-neutral-400">
                적용됨 (저장 권위는 backend)
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
