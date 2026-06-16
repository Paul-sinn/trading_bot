import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export type ButtonVariant = "primary" | "buy" | "danger" | "text";

// UI_GUIDE 버튼 규칙. 매수=녹색, 매도/정지=적색, 기본=흰색.
const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "rounded-lg bg-white text-black hover:bg-neutral-200",
  buy: "rounded-lg bg-[#22c55e] text-black hover:bg-green-400",
  danger: "rounded-lg bg-[#ef4444] text-white hover:bg-red-600",
  text: "text-neutral-500 hover:text-neutral-300",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({
  variant = "primary",
  className,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    />
  );
}
