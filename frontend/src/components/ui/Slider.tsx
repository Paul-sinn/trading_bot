"use client";

import { cn } from "@/lib/utils";

interface SliderProps {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange?: (value: number) => void;
  /** 공격적↔보수적 양 끝 라벨 */
  leftLabel?: string;
  rightLabel?: string;
  className?: string;
}

// UI_GUIDE 슬라이더: 트랙 bg-neutral-800, 핸들 bg-white(accent), 양 끝 라벨 명시.
export function Slider({
  value,
  min = 0,
  max = 100,
  step = 1,
  onChange,
  leftLabel = "보수적",
  rightLabel = "공격적",
  className,
}: SliderProps) {
  return (
    <div className={cn("space-y-2", className)}>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange?.(Number(e.target.value))}
        className="h-1 w-full cursor-pointer appearance-none rounded-full bg-neutral-800 accent-white"
      />
      <div className="flex justify-between text-xs text-neutral-500">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
    </div>
  );
}
