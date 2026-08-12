import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function AppPage({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <main className={cn("min-h-dvh bg-background px-4 pt-7 pb-28", className)}>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-8">{children}</div>
    </main>
  );
}
