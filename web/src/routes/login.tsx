import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/login")({
  component: () => (
    <main className="flex min-h-dvh items-center justify-center bg-muted px-4">
      <section className="w-full max-w-sm rounded-3xl border bg-card p-7 text-card-foreground shadow-sm">
        <h1 className="text-2xl font-bold">访问 ComicLens</h1>
        <p className="mt-2 text-sm text-muted-foreground">访问门禁启用后在此登录。</p>
      </section>
    </main>
  ),
});
