import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeftIcon, UserRoundIcon } from "lucide-react";

import { AppPage } from "@/components/app-page";
import { ComicGrid } from "@/components/comic-card";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState } from "@/components/query-state";
import { buttonVariants } from "@/components/ui/button";
import type { ComicCreatorKind } from "@/domain/api";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";
import { positivePage } from "@/lib/route-search";

export const Route = createFileRoute("/_app/explore_/creator/$kind/$creatorId")({
  validateSearch: (search: Record<string, unknown>) => ({
    page: positivePage(search.page),
  }),
  component: CreatorArchivePage,
});

function CreatorArchivePage() {
  const { kind, creatorId } = Route.useParams();
  const { page } = Route.useSearch();
  const navigate = useNavigate();
  const creatorKind = parseCreatorKind(kind);
  const archive = useQuery({
    queryKey: queryKeys.creator(kind, creatorId, page),
    queryFn: () => {
      if (!creatorKind) throw new Error("归档类型无效");
      return api.creator(creatorKind, creatorId, page);
    },
    enabled: creatorKind !== null,
    ...queryTimes.catalog,
  });

  if (!creatorKind) {
    return (
      <AppPage>
        <ArchiveHeader kind={null} label={null} />
        <EmptyState title="归档类型无效" description="请选择有效的作者或绘者归档。" />
      </AppPage>
    );
  }

  return (
    <AppPage>
      <ArchiveHeader kind={creatorKind} label={archive.data?.label ?? null} />

      {archive.isPending ? (
        <LoadingState label={`正在读取${creatorKind === "author" ? "作者" : "绘者"}作品…`} />
      ) : archive.isError ? (
        <ErrorState error={archive.error} retry={() => void archive.refetch()} />
      ) : archive.data.result.items.length === 0 ? (
        <EmptyState title="这里还没有 Comic" description="该归档目前没有可显示的作品。" />
      ) : (
        <section className="space-y-6">
          <ComicGrid comics={archive.data.result.items} />
          <Pagination
            {...archive.data.result}
            onPage={(nextPage) =>
              void navigate({
                to: "/explore/creator/$kind/$creatorId",
                params: { kind: creatorKind, creatorId },
                search: { page: nextPage },
              })
            }
          />
        </section>
      )}
    </AppPage>
  );
}

function ArchiveHeader({ kind, label }: { kind: ComicCreatorKind | null; label: string | null }) {
  const kindLabel = kind === "author" ? "作者" : kind === "artist" ? "绘者" : "人物";
  return (
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
          <UserRoundIcon className="size-3.5" /> {kindLabel}作品
        </p>
        <h1 className="truncate text-3xl font-bold tracking-tight">{label ?? "作品归档"}</h1>
      </div>
    </header>
  );
}

function parseCreatorKind(value: string): ComicCreatorKind | null {
  return value === "author" || value === "artist" ? value : null;
}
