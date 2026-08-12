import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { BarChart3Icon, CompassIcon, SearchIcon } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AppPage } from "@/components/app-page";
import { ComicGrid } from "@/components/comic-card";
import { ErrorState, LoadingState } from "@/components/query-state";
import { SectionHeading } from "@/components/section-heading";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";

export const Route = createFileRoute("/_app/explore")({
  validateSearch: (search: Record<string, unknown>) => ({
    mode: search.mode === "latest" ? "latest" : undefined,
  }),
  component: ExplorePage,
});

function ExplorePage() {
  const navigate = useNavigate();
  const { mode } = Route.useSearch();
  const [term, setTerm] = useState("");
  const categories = useQuery({
    queryKey: queryKeys.categories,
    queryFn: api.categories,
    ...queryTimes.categories,
  });
  const latest = useQuery({
    queryKey: ["latest", 1],
    queryFn: () => api.latest(1),
    ...queryTimes.catalog,
    enabled: mode === "latest",
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const q = term.trim();
    if (q) void navigate({ to: "/explore/search", search: { q, page: 1 } });
  }

  return (
    <AppPage>
      <header className="space-y-3">
        <div className="flex size-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <CompassIcon className="size-5" />
        </div>
        <h1 className="text-4xl font-bold tracking-tight">探索</h1>
        <p className="text-muted-foreground">搜索、全部分类、三种排序和 Manga18fx 一周热门。</p>
      </header>

      <form onSubmit={submit} className="flex gap-2 rounded-2xl border bg-card p-2 shadow-sm">
        <Input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="搜索 Comic 标题"
          aria-label="搜索词"
          className="border-0 focus:ring-0"
        />
        <Button type="submit" size="icon" aria-label="搜索">
          <SearchIcon className="size-4" />
        </Button>
      </form>

      <Link
        to="/explore/ranking"
        search={{ page: 1 }}
        className="flex items-center justify-between rounded-3xl bg-primary p-5 text-primary-foreground shadow-lg shadow-black/10"
      >
        <span>
          <span className="block text-xs font-medium opacity-70">Manga18fx</span>
          <span className="mt-1 block text-xl font-semibold">一周热门排行</span>
        </span>
        <BarChart3Icon className="size-8" />
      </Link>

      {categories.isPending ? (
        <LoadingState label="正在读取全部分类…" />
      ) : categories.isError ? (
        <ErrorState error={categories.error} retry={() => void categories.refetch()} />
      ) : (
        <section className="space-y-4">
          <SectionHeading title={`全部分类 · ${categories.data.length}`} />
          <div className="flex flex-wrap gap-2">
            {categories.data.map((category) => (
              <Link
                key={category.categoryId}
                to="/explore/category/$categoryId"
                params={{ categoryId: category.categoryId }}
                search={{ page: 1, order: "latest" }}
                className={buttonVariants({
                  variant: category.kind === "source_special" ? "default" : "outline",
                  size: "sm",
                })}
              >
                {category.label}
              </Link>
            ))}
          </div>
        </section>
      )}

      {mode === "latest" && (
        <section className="space-y-5">
          <SectionHeading title="最新更新" />
          {latest.isPending ? (
            <LoadingState />
          ) : latest.isError ? (
            <ErrorState error={latest.error} retry={() => void latest.refetch()} />
          ) : (
            <ComicGrid comics={latest.data.items} />
          )}
        </section>
      )}
    </AppPage>
  );
}
