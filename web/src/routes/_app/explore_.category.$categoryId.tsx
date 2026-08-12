import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeftIcon, TagsIcon } from "lucide-react";

import { AppPage } from "@/components/app-page";
import { ComicGrid } from "@/components/comic-card";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState } from "@/components/query-state";
import { buttonVariants } from "@/components/ui/button";
import type { ComicOrder } from "@/domain/api";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { queryKeys, queryTimes } from "@/lib/query-keys";
import { comicOrder, positivePage } from "@/lib/route-search";

const orderLabels: Record<ComicOrder, string> = {
  latest: "最新更新",
  rating: "评分最高",
  views: "最多浏览",
};

export const Route = createFileRoute("/_app/explore_/category/$categoryId")({
  validateSearch: (search: Record<string, unknown>) => ({
    page: positivePage(search.page),
    order: comicOrder(search.order),
  }),
  component: CategoryPage,
});

function CategoryPage() {
  const { categoryId } = Route.useParams();
  const { page, order } = Route.useSearch();
  const navigate = useNavigate();
  const categories = useQuery({
    queryKey: queryKeys.categories,
    queryFn: api.categories,
    ...queryTimes.categories,
  });
  const category = categories.data?.find((item) => item.categoryId === categoryId);
  const selectedOrder = category?.supportedOrders.includes(order) === false ? "latest" : order;
  const results = useQuery({
    queryKey: queryKeys.category(categoryId, page, selectedOrder),
    queryFn: () => api.category(categoryId, page, selectedOrder),
    ...queryTimes.catalog,
  });

  function change(nextPage: number, nextOrder: ComicOrder = selectedOrder) {
    void navigate({
      to: "/explore/category/$categoryId",
      params: { categoryId },
      search: { page: nextPage, order: nextOrder },
    });
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
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <TagsIcon className="size-3.5" /> 分类
          </p>
          <h1 className="truncate text-3xl font-bold tracking-tight">
            {category?.label ?? categoryId}
          </h1>
        </div>
      </header>

      <div className="flex flex-wrap gap-2">
        {(category?.supportedOrders ?? (["latest", "rating", "views"] as ComicOrder[])).map(
          (value) => (
            <button
              type="button"
              key={value}
              onClick={() => change(1, value)}
              className={cn(
                buttonVariants({ variant: value === selectedOrder ? "default" : "outline" }),
              )}
            >
              {orderLabels[value]}
            </button>
          ),
        )}
      </div>

      {results.isPending ? (
        <LoadingState />
      ) : results.isError ? (
        <ErrorState error={results.error} retry={() => void results.refetch()} />
      ) : results.data.items.length === 0 ? (
        <EmptyState title="这一页没有 Comic" description="返回上一页或选择其他分类。" />
      ) : (
        <section className="space-y-6">
          <ComicGrid comics={results.data.items} />
          <Pagination {...results.data} onPage={(nextPage) => change(nextPage)} />
        </section>
      )}
    </AppPage>
  );
}
