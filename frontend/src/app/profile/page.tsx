// ⑥ 투자성향 설정 — 공격적↔보수적 슬라이더 + 사이징 미리보기, 섹터 화이트/블랙리스트,
// 매매 시간대, 알림(슬랙/SMS) 설정. 슬라이더/토글 인터랙션이 있어 Client Component.
// 기본값은 mockRiskProfile(step 0).
// CRITICAL(CLAUDE.md/ADR-001): 저장 권위는 backend. UI는 입력/미리보기까지만 하고,
// 슬라이더/토글 값을 실제 매매 파라미터에 직접 적용하지 않는다(저장은 후속 step에서 backend 연동).
// 미리보기 계산은 algorithms/sizing.py(risk_appetite_weight) 매핑을 표시용으로만 반영한다.
"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { Slider } from "@/components/ui/Slider";
import { Toggle } from "@/components/ui/Toggle";
import { mockRiskProfile } from "@/lib/mock";

// 표시 전용 사이징 미리보기. 실제 적용은 backend(sizing.py) 권위.
// 포지션 가중치 = 0.5 + 0.5*appetite (sizing.risk_appetite_weight와 동일 매핑).
function previewWeight(appetite: number): number {
  return 0.5 + 0.5 * appetite;
}

// 스탑로스 ATR 배수(표시용): 공격적일수록 넓은 스탑.
function previewStopMultiplier(appetite: number): number {
  return 1.5 + 1.5 * appetite;
}

export default function ProfilePage() {
  const [appetite, setAppetite] = useState(mockRiskProfile.risk_appetite);
  const [whitelist, setWhitelist] = useState(
    mockRiskProfile.sector_whitelist.join(", "),
  );
  const [blacklist, setBlacklist] = useState(
    mockRiskProfile.sector_blacklist.join(", "),
  );
  const [startTime, setStartTime] = useState("09:30");
  const [endTime, setEndTime] = useState("16:00");
  const [slackOn, setSlackOn] = useState(true);
  const [smsOn, setSmsOn] = useState(false);

  // 슬라이더 값(0~100)을 appetite(0.0~1.0)로 정규화.
  const a = appetite / 100;
  const weight = previewWeight(a);
  const stopMultiplier = previewStopMultiplier(a);

  const inputClass =
    "w-full rounded-lg border border-neutral-800 bg-[#1a1a1a] px-4 py-3 text-sm text-white";

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold text-white">투자성향 설정</h1>

      {/* 공격적↔보수적 슬라이더 + 사이징 미리보기 */}
      <Card className="space-y-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-medium text-neutral-400">투자성향</h2>
          <span className="text-sm tabular-nums text-neutral-300">
            {appetite}
          </span>
        </div>
        <Slider
          value={appetite}
          onChange={setAppetite}
          leftLabel="보수적"
          rightLabel="공격적"
        />

        <div className="grid grid-cols-2 gap-3 border-t border-neutral-800 pt-4">
          <div className="space-y-1">
            <p className="text-xs text-neutral-500">예상 포지션 가중치</p>
            <p
              data-testid="preview-weight"
              className="text-lg font-semibold tabular-nums text-white"
            >
              ×{weight.toFixed(2)}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-neutral-500">스탑로스 ATR 배수</p>
            <p
              data-testid="preview-stop"
              className="text-lg font-semibold tabular-nums text-white"
            >
              ×{stopMultiplier.toFixed(1)}
            </p>
          </div>
        </div>
        <p className="text-xs text-neutral-500">
          미리보기는 표시 전용입니다. 실제 사이징 적용 권위는 backend입니다.
        </p>
      </Card>

      {/* 섹터 화이트리스트 / 블랙리스트 */}
      <Card className="space-y-4">
        <h2 className="text-sm font-medium text-neutral-400">섹터 설정</h2>

        <div className="space-y-2">
          <label
            htmlFor="sector-whitelist"
            className="block text-sm font-medium text-neutral-400"
          >
            화이트리스트 (선호 섹터, 쉼표 구분)
          </label>
          <input
            id="sector-whitelist"
            data-testid="sector-whitelist"
            type="text"
            value={whitelist}
            onChange={(e) => setWhitelist(e.target.value)}
            className={inputClass}
          />
        </div>

        <div className="space-y-2">
          <label
            htmlFor="sector-blacklist"
            className="block text-sm font-medium text-neutral-400"
          >
            블랙리스트 (제외 섹터, 쉼표 구분)
          </label>
          <input
            id="sector-blacklist"
            data-testid="sector-blacklist"
            type="text"
            value={blacklist}
            onChange={(e) => setBlacklist(e.target.value)}
            className={inputClass}
          />
        </div>
      </Card>

      {/* 매매 시간대 */}
      <Card className="space-y-4">
        <h2 className="text-sm font-medium text-neutral-400">매매 시간대</h2>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <label
              htmlFor="time-start"
              className="block text-sm font-medium text-neutral-400"
            >
              시작
            </label>
            <input
              id="time-start"
              data-testid="time-start"
              type="time"
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
              className={`${inputClass} tabular-nums`}
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor="time-end"
              className="block text-sm font-medium text-neutral-400"
            >
              종료
            </label>
            <input
              id="time-end"
              data-testid="time-end"
              type="time"
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
              className={`${inputClass} tabular-nums`}
            />
          </div>
        </div>
      </Card>

      {/* 알림 설정 (슬랙 / SMS) */}
      <Card className="space-y-4">
        <h2 className="text-sm font-medium text-neutral-400">알림 설정</h2>
        <div className="flex items-center justify-between">
          <span className="text-sm text-neutral-300">슬랙</span>
          <div data-testid="toggle-slack">
            <Toggle checked={slackOn} onChange={setSlackOn} label="슬랙 알림" />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-neutral-300">SMS</span>
          <div data-testid="toggle-sms">
            <Toggle checked={smsOn} onChange={setSmsOn} label="SMS 알림" />
          </div>
        </div>
        <p className="text-xs text-neutral-500">
          설정 저장 권위는 backend입니다. 입력값은 후속 단계에서 연동됩니다.
        </p>
      </Card>
    </div>
  );
}
