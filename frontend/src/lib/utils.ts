/** className 병합 헬퍼. falsy 값을 걸러 공백으로 합친다 (외부 의존성 없음). */
export function cn(
  ...classes: Array<string | false | null | undefined>
): string {
  return classes.filter(Boolean).join(" ");
}
