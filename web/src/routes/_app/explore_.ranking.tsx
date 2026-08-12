import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeftIcon, TrophyIcon } from "lucide-react";

import { AppPage } from "@/components/app-page";
import { ComicGrid } from "@/components/comic-card";
import { Pagination } from "@/components/pagination";
import { ErrorState, LoadingState } from "@/components/query-state";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";
import { positivePage } from "@/lib/route-search";

export const Route = createFileRoute("/_app/explore_/ranking")({
  validateSearch: (search: Record<string, unknown>) => ({ page: positivePage(search.page) }),
  component: RankingPage,
});

function RankingPage() {
  const { page } = Route.useSearch();
  const navigate = useNavigate();
  const ranking = useQuery({
    queryKey: queryKeys.ranking(page),
    queryFn: () => api.ranking(page),
    ...queryTimes.catalog,
  });

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
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <TrophyIcon className="size-3.5" /> Manga18fx
          </p>
          <h1 className="text-3xl font-bold tracking-tight">一周热门排行</h1>
        </div>
      </header>

      {ranking.isPending ? (
        <LoadingState label="正在读取排行…" />
      ) : ranking.isError ? (
        <ErrorState error={ranking.error} retry={() => void ranking.refetch()} />
      ) : (
        <section className="space-y-6">
          <ComicGrid comics={ranking.data.result.items} />
          <Pagination
            {...ranking.data.result}
            onPage={(nextPage) =>
              void navigate({ to: "/explore/ranking", search: { page: nextPage } })
            }
          />
        </section>
      )}
    </AppPage>
  );
}
