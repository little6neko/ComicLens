import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeftIcon, BookOpenIcon, HeartIcon, LoaderCircleIcon, StarIcon } from "lucide-react";
import { toast } from "sonner";

import { AppPage } from "@/components/app-page";
import { ErrorState, LoadingState } from "@/components/query-state";
import { Button, buttonVariants } from "@/components/ui/button";
import { resolveComicReadingTarget } from "@/features/comic-detail/reading-target";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_app/comic/$comicId")({
  component: ComicDetailPage,
});

function ComicDetailPage() {
  const { comicId } = Route.useParams();
  const queryClient = useQueryClient();
  const comic = useQuery({
    queryKey: queryKeys.comic(comicId),
    queryFn: () => api.comic(comicId),
    ...queryTimes.detail,
  });
  const favorites = useQuery({ queryKey: queryKeys.favorites, queryFn: api.favorites });
  const history = useQuery({ queryKey: queryKeys.history, queryFn: api.history });
  const readChapters = useQuery({
    queryKey: queryKeys.readChapters(comicId),
    queryFn: () => api.readChapters(comicId),
  });
  const isFavorite = favorites.data?.some((item) => item.comic.comicId === comicId) ?? false;

  const toggleFavorite = useMutation({
    mutationFn: async () => {
      if (isFavorite) return api.deleteFavorite(comicId);
      if (!comic.data) throw new Error("Comic 尚未加载完成");
      return api.saveFavorite(comicId, comic.data);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.favorites });
      toast.success(isFavorite ? "已取消收藏" : "已加入收藏");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "操作失败"),
  });

  if (comic.isPending) {
    return (
      <AppPage>
        <LoadingState label="正在读取 Comic 详情…" />
      </AppPage>
    );
  }
  if (comic.isError) {
    return (
      <AppPage>
        <ErrorState error={comic.error} retry={() => void comic.refetch()} />
      </AppPage>
    );
  }

  const detail = comic.data;
  const progress = history.data?.find((item) => item.comic.comicId === comicId);
  const readingTarget = resolveComicReadingTarget(detail, progress);
  const readSet = new Set(readChapters.data?.chapterIds ?? []);

  return (
    <AppPage>
      <header>
        <Link to="/" className={buttonVariants({ variant: "outline", size: "icon" })}>
          <ArrowLeftIcon className="size-4" />
          <span className="sr-only">返回首页</span>
        </Link>
      </header>

      <section className="grid gap-7 md:grid-cols-[16rem_minmax(0,1fr)] md:items-start">
        <div className="mx-auto w-full max-w-64 overflow-hidden rounded-3xl bg-muted shadow-xl shadow-black/10 md:mx-0">
          <img
            src={detail.coverUrl}
            alt={detail.title}
            className="aspect-[3/4] size-full object-cover"
          />
        </div>
        <div className="space-y-5">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              {detail.rating !== null && (
                <span className="flex items-center gap-1">
                  <StarIcon className="size-4 fill-current" /> {detail.rating.toFixed(1)}
                </span>
              )}
              {detail.status && <span>· {detail.status}</span>}
              {detail.comicType && <span>· {detail.comicType}</span>}
            </div>
            <h1 className="mt-2 text-3xl font-bold tracking-[-0.035em] sm:text-5xl">
              {detail.title}
            </h1>
            {detail.alternativeTitles.length > 0 && (
              <p className="mt-3 text-sm text-muted-foreground">
                {detail.alternativeTitles.join(" · ")}
              </p>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {readingTarget && (
              <a
                href={`/reader/${encodeURIComponent(comicId)}/${encodeURIComponent(readingTarget.chapterId)}?page=${readingTarget.page}`}
                className={buttonVariants({ size: "lg" })}
              >
                <BookOpenIcon className="size-4" /> {readingTarget.label}
              </a>
            )}
            <Button
              variant={isFavorite ? "secondary" : "outline"}
              size="lg"
              disabled={toggleFavorite.isPending}
              onClick={() => toggleFavorite.mutate()}
            >
              {toggleFavorite.isPending ? (
                <LoaderCircleIcon className="size-4 animate-spin" />
              ) : (
                <HeartIcon className={cn("size-4", isFavorite && "fill-current")} />
              )}
              {isFavorite ? "已收藏" : "收藏"}
            </Button>
          </div>

          {progress && (
            <p className="text-sm text-muted-foreground">
              {progress.chapterTitle} · 第 {progress.pageIndex + 1} / {progress.totalPages} 页
            </p>
          )}

          {detail.genres.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {detail.genres.map((genre) => (
                <span key={genre} className="rounded-full bg-muted px-3 py-1.5 text-xs font-medium">
                  {genre}
                </span>
              ))}
            </div>
          )}

          {(detail.authors.length > 0 || detail.artists.length > 0 || detail.releaseLabel) && (
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              {detail.authors.length > 0 && (
                <div>
                  <dt className="text-muted-foreground">作者</dt>
                  <dd>{detail.authors.join("、")}</dd>
                </div>
              )}
              {detail.artists.length > 0 && (
                <div>
                  <dt className="text-muted-foreground">绘者</dt>
                  <dd>{detail.artists.join("、")}</dd>
                </div>
              )}
              {detail.releaseLabel && (
                <div>
                  <dt className="text-muted-foreground">发行日期</dt>
                  <dd>{detail.releaseLabel}</dd>
                </div>
              )}
            </dl>
          )}

          {detail.summary && (
            <p className="whitespace-pre-line leading-7 text-muted-foreground">{detail.summary}</p>
          )}
        </div>
      </section>

      <section className="space-y-4">
        <div className="flex items-end justify-between">
          <h2 className="text-2xl font-semibold tracking-tight">章节</h2>
          <span className="text-sm text-muted-foreground">{detail.chapters.length} 话</span>
        </div>
        <div className="divide-y overflow-hidden rounded-3xl border bg-card">
          {detail.chapters.map((chapter) => {
            const wasRead = readSet.has(chapter.chapterId);
            return (
              <a
                key={chapter.chapterId}
                href={`/reader/${encodeURIComponent(comicId)}/${encodeURIComponent(chapter.chapterId)}`}
                aria-label={`${chapter.title}${wasRead ? "（已读）" : ""}`}
                className={cn(
                  "flex items-center justify-between gap-4 px-5 py-4 outline-none transition-colors hover:bg-muted/60 focus-visible:bg-accent focus-visible:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  wasRead &&
                    "bg-muted text-muted-foreground ring-1 ring-muted-foreground/20 ring-inset hover:bg-muted/70 hover:text-foreground",
                )}
              >
                <span className="min-w-0 truncate font-medium">{chapter.title}</span>
                <span className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                  {chapter.updatedLabel}
                </span>
              </a>
            );
          })}
        </div>
      </section>
    </AppPage>
  );
}
