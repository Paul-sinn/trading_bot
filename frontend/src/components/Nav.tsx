"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// PRD 6개 페이지 라우트. 좌측 정렬 사이드 내비.
const NAV_ITEMS = [
  { href: "/", label: "대시보드" },
  { href: "/daily", label: "일간 거래기록" },
  { href: "/weekly", label: "주간 거래기록" },
  { href: "/direction", label: "방향성 & AI" },
  { href: "/goals", label: "목표 & 리스크" },
  { href: "/shadow", label: "섀도 리포트" },
  { href: "/profile", label: "투자성향 설정" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="flex w-56 shrink-0 flex-col gap-1 border-r border-neutral-800 bg-[#0a0a0a] p-4">
      <div className="mb-4 px-2 text-sm font-semibold text-white">
        Trading Bot
      </div>
      {NAV_ITEMS.map((item) => {
        const active = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "rounded-lg px-3 py-2 text-sm transition-colors",
              active
                ? "bg-[#1a1a1a] text-white"
                : "text-neutral-400 hover:text-neutral-200",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
