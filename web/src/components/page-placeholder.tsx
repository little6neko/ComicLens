import type { LucideIcon } from "lucide-react";

import { AppPage } from "@/components/app-page";

export function PagePlaceholder({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
}) {
  return (
    <AppPage>
      <header className="space-y-2">
        <div className="flex size-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <Icon className="size-5" />
        </div>
        <h1 className="text-4xl font-bold tracking-tight">{title}</h1>
        <p className="max-w-2xl text-muted-foreground">{description}</p>
      </header>
      <section className="rounded-3xl border bg-card p-8 text-card-foreground shadow-sm">
        <p className="text-sm text-muted-foreground">ComicLens 正在连接服务器能力。</p>
      </section>
    </AppPage>
  );
}
