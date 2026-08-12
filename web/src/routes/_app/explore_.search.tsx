import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeftIcon, SearchIcon } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { AppPage } from "@/components/app-page";
import { ComicGrid } from "@/components/comic-card";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState } from "@/components/query-state";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";
import { positivePage } from "@/lib/route-search";

export const Route = createFileRoute("/_app/explore_/search")({
  validateSearch: (search: Record<string, unknown>) => ({
    q: typeof search.q === "string" ? search.q.trim() : "",
    page: positivePage(search.page),
  }),
  component: SearchPage,
});

function SearchPage() {
  const navigate = useNavigate();
  const { q, page } = Route.useSearch();
  const [term, setTerm] = useState(q);
  const results = useQuery({
    queryKey: queryKeys.search(q, page),
    queryFn: () => api.search(q, page),
    enabled: q.length > 0,
    ...queryTimes.catalog,
  });

  useEffect(() => setTerm(q), [q]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = term.trim();
    if (next) void navigate({ to: "/explore/search", search: { q: next, page: 1 } });
  }

  return (
    <AppPage>
      <header className="flex items-center gap-3">
        <Link
          to="/explore"
          search={{ mode: undefined }}
          className={buttonVariants({ variant: "outline", size: "icon" })}
        >
          <ArrowLeftIcon className="size-4" />
          <span className="sr-only">返回探索</span>
        </Link>
        <div>
          <p className="text-xs font-medium text-muted-foreground">搜索</p>
          <h1 className="text-3xl font-bold tracking-tight">{q || "Comic"}</h1>
        </div>
      </header>

      <form onSubmit={submit} className="flex gap-2 rounded-2xl border bg-card p-2 shadow-sm">
        <Input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="输入 Comic 标题"
          aria-label="搜索词"
          className="border-0 focus:ring-0"
        />
        <Button type="submit" size="icon" aria-label="搜索">
          <SearchIcon className="size-4" />
        </Button>
      </form>

      {!q ? (
        <EmptyState title="输入搜索词" description="支持 Manga18fx 的站内标题搜索。" />
      ) : results.isPending ? (
        <LoadingState label="正在搜索…" />
      ) : results.isError ? (
        <ErrorState error={results.error} retry={() => void results.refetch()} />
      ) : results.data.items.length === 0 ? (
        <EmptyState title="没有找到结果" description="换一个标题或更短的关键词试试。" />
      ) : (
        <section className="space-y-6">
          <p className="text-sm text-muted-foreground">第 {results.data.page} 页</p>
          <ComicGrid comics={results.data.items} />
          <Pagination
            {...results.data}
            onPage={(nextPage) =>
              void navigate({ to: "/explore/search", search: { q, page: nextPage } })
            }
          />
        </section>
      )}
    </AppPage>
  );
}
