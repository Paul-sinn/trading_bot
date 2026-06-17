// ④ 방향성 & AI 분석 — 매일 9시 Claude 시황 요약 + 다음 7일 예상 방향(강세/중립/약세).
// Server Component. 데이터: getDirection()(REST) 시도 → 실패 시 mockDirection 폴백.
// CRITICAL(CLAUDE.md/ADR-005): Claude는 backend에서만 호출한다. 프론트는 결과만 표시.
// 색상은 UI_GUIDE 방향성 팔레트(강세 녹색/중립 중립색/약세 적색)만 사용한다.
import { Card } from "@/components/ui/Card";
import { getDirection } from "@/lib/api";
import { mockDirection } from "@/lib/mock";
import type { DirectionLabel } from "@/types";

// 방향성 라벨 → 한글 표기 + 시맨틱 색상. 알 수 없는 값은 중립으로 안전 처리.
const DIRECTION_META: Record<DirectionLabel, { text: string; color: string }> = {
  bullish: { text: "강세", color: "text-[#22c55e]" },
  neutral: { text: "중립", color: "text-neutral-400" },
  bearish: { text: "약세", color: "text-[#ef4444]" },
};

export default async function DirectionPage() {
  const direction = (await getDirection()) ?? mockDirection;
  const meta = DIRECTION_META[direction.label] ?? DIRECTION_META.neutral;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <h1 className="text-2xl font-semibold text-white">방향성 & AI 분석</h1>

      <Card>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-medium text-neutral-400">시황 요약</h2>
          <span className="text-xs tabular-nums text-neutral-500">
            {direction.date} 09:00 생성
          </span>
        </div>
        <p
          data-testid="market-summary"
          className="text-sm leading-relaxed text-neutral-300"
        >
          {direction.summary}
        </p>
      </Card>

      <Card>
        <h2 className="mb-3 text-sm font-medium text-neutral-400">
          다음 7일 예상 방향
        </h2>
        <div
          data-testid="direction-label"
          className={`text-3xl font-semibold ${meta.color}`}
        >
          {meta.text}
        </div>
        <p
          data-testid="direction-rationale"
          className="mt-3 text-sm leading-relaxed text-neutral-300"
        >
          {direction.rationale}
        </p>
      </Card>
    </div>
  );
}
