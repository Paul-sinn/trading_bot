import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

// UI_GUIDE 카드: rounded-lg bg-[#141414] border border-neutral-800 p-6
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-lg border border-neutral-800 bg-[#141414] p-6",
        className,
      )}
      {...props}
    />
  );
}
