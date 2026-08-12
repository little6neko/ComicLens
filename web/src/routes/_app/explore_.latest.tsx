import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeftIcon, Clock3Icon } from "lucide-react";

import { AppPage } from "@/components/app-page";
import { ComicGrid } from "@/components/comic-card";
import { Pagination } from "@/components/pagination";
import { ErrorState, LoadingState } from "@/components/query-state";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api-client";
import { queryTimes } from "@/lib/query-keys";
import { positivePage } from "@/lib/route-search";

export const Route = createFileRoute("/_app/explore_/latest")({
  validateSearch: (search: Record<string, unknown>) => ({ page: positivePage(search.page) }),
  component: LatestPage,
});

function LatestPage() {
  const { page } = Route.useSearch();
  const navigate = useNavigate();
  const latest = useQuery({
    queryKey: ["latest", page],
    queryFn: () => api.latest(page),
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
            <Clock3Icon className="size-3.5" /> Manga18fx
          </p>
          <h1 className="text-3xl font-bold tracking-tight">最新更新</h1>
        </div>
      </header>

      {latest.isPending ? (
        <LoadingState />
      ) : latest.isError ? (
        <ErrorState error={latest.error} retry={() => void latest.refetch()} />
      ) : (
        <section className="space-y-6">
          <ComicGrid comics={latest.data.items} />
          <Pagination
            {...latest.data}
            onPage={(nextPage) =>
              void navigate({ to: "/explore/latest", search: { page: nextPage } })
            }
          />
        </section>
      )}
    </AppPage>
  );
}
