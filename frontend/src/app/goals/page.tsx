// ⑤ 목표 & 리스크 — 목표금액 진행 바 + 드로우다운/최대 포지션 한도 설정.
// 입력 인터랙션이 있어 Client Component. 기본값은 mockGoals(step 0).
// CRITICAL(CLAUDE.md/ADR-001): 설정 저장 권위는 backend. UI는 입력/표시까지만 하고,
// 이 값을 거래 로직에 직접 적용하지 않는다(저장은 후속 step에서 backend 연동).
// 한도 값은 agents/risk.py RiskLimits(max_drawdown_pct/max_position_pct)와 매핑한다.
"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { Gauge } from "@/components/ui/Gauge";
import { mockGoals } from "@/lib/mock";
import { formatUsd } from "@/lib/utils";

// 목표 대비 진행률(%) = 현재 / 목표 × 100. 목표 <= 0이면 0(안전 처리).
function progressPct(current: number, target: number): number {
  if (target <= 0) return 0;
  return (current / target) * 100;
}

export default function GoalsPage() {
  // RiskLimits 매핑 한도는 입력으로 조정(로컬 상태). 진행 금액은 표시 전용(mock).
  const [maxDrawdownPct, setMaxDrawdownPct] = useState(
    String(mockGoals.max_drawdown_pct),
  );
  const [maxPositionPct, setMaxPositionPct] = useState(
    String(mockGoals.max_position_pct),
  );

  const progress = progressPct(
    mockGoals.current_amount,
    mockGoals.target_amount,
  );

  const inputClass =
    "w-full rounded-lg border border-neutral-800 bg-[#1a1a1a] px-4 py-3 text-sm tabular-nums text-white";

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold text-white">목표 &amp; 리스크</h1>

      {/* 목표금액 진행 바 */}
      <Card className="space-y-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-medium text-neutral-400">목표금액 진행</h2>
          <span className="text-sm tabular-nums text-neutral-300">
            {formatUsd(mockGoals.current_amount)} /{" "}
            {formatUsd(mockGoals.target_amount)}
          </span>
        </div>
        <div data-testid="goal-progress">
          <Gauge value={progress} label="진행률" />
        </div>
      </Card>

      {/* 드로우다운 한도 / 최대 포지션 크기 설정 (RiskLimits 매핑) */}
      <Card className="space-y-4">
        <h2 className="text-sm font-medium text-neutral-400">리스크 한도</h2>

        <div className="space-y-2">
          <label
            htmlFor="max-drawdown"
            className="block text-sm font-medium text-neutral-400"
          >
            드로우다운 한도 (%)
          </label>
          <input
            id="max-drawdown"
            data-testid="input-drawdown"
            type="number"
            inputMode="numeric"
            value={maxDrawdownPct}
            onChange={(e) => setMaxDrawdownPct(e.target.value)}
            className={inputClass}
          />
        </div>

        <div className="space-y-2">
          <label
            htmlFor="max-position"
            className="block text-sm font-medium text-neutral-400"
          >
            최대 포지션 크기 (%)
          </label>
          <input
            id="max-position"
            data-testid="input-max-position"
            type="number"
            inputMode="numeric"
            value={maxPositionPct}
            onChange={(e) => setMaxPositionPct(e.target.value)}
            className={inputClass}
          />
        </div>

        <p className="text-xs text-neutral-500">
          저장 권위는 backend입니다. 입력값은 후속 단계에서 연동됩니다.
        </p>
      </Card>
    </div>
  );
}
