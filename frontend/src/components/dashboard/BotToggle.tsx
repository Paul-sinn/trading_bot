"use client";

import { useState } from "react";
import { Toggle } from "@/components/ui/Toggle";

interface BotToggleProps {
  /** backend 권위 상태의 초기값(현재 mock). 후속 step에서 WS로 동기화한다. */
  initialOn: boolean;
}

// 봇 ON/OFF. 상태 권위는 backend지만 이 step에서는 로컬 UI 상태만 낙관적 반영한다.
export function BotToggle({ initialOn }: BotToggleProps) {
  const [on, setOn] = useState(initialOn);
  return (
    <div className="flex items-center gap-3">
      <Toggle checked={on} onChange={setOn} label="봇 ON/OFF" />
      <span className="text-sm tabular-nums text-neutral-300">
        {on ? "ON" : "OFF"}
      </span>
    </div>
  );
}
