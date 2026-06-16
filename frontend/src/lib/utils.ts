/** className 병합 헬퍼. falsy 값을 걸러 공백으로 합친다 (외부 의존성 없음). */
export function cn(
  ...classes: Array<string | false | null | undefined>
): string {
  return classes.filter(Boolean).join(" ");
}

/** USD 통화 표기($1,234.56). 손익 등 부호가 필요한 경우 `signed`로 +/− 접두. */
export function formatUsd(value: number, signed = false): string {
  const abs = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "−" : "") : value < 0 ? "−" : "";
  return `${sign}$${abs}`;
}

/** 손익 값에 대한 시맨틱 텍스트 색상 클래스(상승 녹색 / 하락 적색 / 0 중립). */
export function pnlColorClass(value: number): string {
  if (value > 0) return "text-[#22c55e]";
  if (value < 0) return "text-[#ef4444]";
  return "text-neutral-400";
}
