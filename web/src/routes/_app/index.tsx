import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowRightIcon, ScanSearchIcon } from "lucide-react";

import { AppPage } from "@/components/app-page";
import { ComicGrid, FeaturedCard } from "@/components/comic-card";
import { ErrorState, LoadingState } from "@/components/query-state";
import { SectionHeading } from "@/components/section-heading";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";

export const Route = createFileRoute("/_app/")({
  component: HomePage,
});

function HomePage() {
  const home = useQuery({ queryKey: queryKeys.home, queryFn: api.home, ...queryTimes.catalog });

  return (
    <AppPage>
      <header className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-4 flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-black/10">
            <ScanSearchIcon className="size-6" />
          </div>
          <h1 className="text-4xl font-bold tracking-[-0.04em] sm:text-5xl">ComicLens</h1>
          <p className="mt-3 max-w-xl text-muted-foreground">
            浏览 Manga18fx 实时更新，打开章节即可按需逐张翻译。
          </p>
        </div>
        <Link
          to="/explore"
          search={{ mode: undefined }}
          className={buttonVariants({ variant: "outline", size: "lg" })}
        >
          探索 Comic <ArrowRightIcon className="size-4" />
        </Link>
      </header>

      {home.isPending ? (
        <LoadingState />
      ) : home.isError ? (
        <ErrorState error={home.error} retry={() => void home.refetch()} />
      ) : (
        <>
          <section className="space-y-4">
            <SectionHeading title="重点更新" />
            <div className="-mx-4 flex gap-4 overflow-x-auto px-4 pb-3 [scrollbar-width:none]">
              {home.data.featured.map((comic) => (
                <FeaturedCard key={comic.comicId} comic={comic} />
              ))}
            </div>
          </section>

          <section className="space-y-5">
            <SectionHeading
              title="最新更新"
              action={
                <Link
                  to="/explore/latest"
                  search={{ page: 1 }}
                  className="text-sm text-muted-foreground hover:text-foreground"
                >
                  查看更多
                </Link>
              }
            />
            <ComicGrid comics={home.data.latest.items} />
          </section>
        </>
      )}
    </AppPage>
  );
}
