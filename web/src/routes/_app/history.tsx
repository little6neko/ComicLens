import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { BookOpenIcon, HistoryIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { AppPage } from "@/components/app-page";
import { EmptyState, ErrorState, LoadingState } from "@/components/query-state";
import { Button, buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export const Route = createFileRoute("/_app/history")({
  component: HistoryPage,
});

function HistoryPage() {
  const queryClient = useQueryClient();
  const history = useQuery({ queryKey: queryKeys.history, queryFn: api.history });
  const remove = useMutation({
    mutationFn: api.deleteHistory,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.history });
      toast.success("已移除阅读记录");
    },
    onError: showError,
  });
  const clear = useMutation({
    mutationFn: api.clearHistory,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.history });
      toast.success("阅读历史已清空");
    },
    onError: showError,
  });

  return (
    <AppPage>
      <header className="flex items-end justify-between gap-4">
        <div>
          <div className="mb-3 flex size-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <HistoryIcon className="size-5" />
          </div>
          <h1 className="text-4xl font-bold tracking-tight">历史</h1>
          <p className="mt-2 text-muted-foreground">继续上次阅读的章节和页码。</p>
        </div>
        {!!history.data?.length && (
          <Button
            variant="outline"
            disabled={clear.isPending}
            onClick={() => {
              if (window.confirm("确定清空全部阅读历史吗？")) clear.mutate();
            }}
          >
            <Trash2Icon className="size-4" /> 清空
          </Button>
        )}
      </header>

      {history.isPending ? (
        <LoadingState label="正在读取历史…" />
      ) : history.isError ? (
        <ErrorState error={history.error} retry={() => void history.refetch()} />
      ) : history.data.length === 0 ? (
        <EmptyState title="暂无阅读历史" description="打开任意章节后，阅读进度会保存在这里。" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {history.data.map((item) => {
            const progress = Math.round(((item.pageIndex + 1) / item.totalPages) * 100);
            return (
              <article
                key={item.comic.comicId}
                className="flex gap-4 rounded-3xl border bg-card p-4 shadow-sm"
              >
                <a
                  href={`/comic/${encodeURIComponent(item.comic.comicId)}`}
                  className="w-24 shrink-0 overflow-hidden rounded-2xl bg-muted"
                >
                  <img
                    src={item.comic.coverUrl}
                    alt={item.comic.title}
                    className="aspect-[3/4] size-full object-cover"
                    loading="lazy"
                  />
                </a>
                <div className="flex min-w-0 flex-1 flex-col">
                  <h2 className="line-clamp-2 font-semibold">{item.comic.title}</h2>
                  <p className="mt-1 truncate text-sm text-muted-foreground">{item.chapterTitle}</p>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    第 {item.pageIndex + 1} / {item.totalPages} 页 · {progress}%
                  </p>
                  <div className="mt-auto flex items-center gap-2 pt-4">
                    <a
                      href={`/reader/${encodeURIComponent(item.comic.comicId)}/${encodeURIComponent(item.chapterId)}?page=${item.pageIndex + 1}`}
                      className={buttonVariants({ size: "sm" })}
                    >
                      <BookOpenIcon className="size-3.5" /> 继续
                    </a>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-muted-foreground"
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(item.comic.comicId)}
                    >
                      <Trash2Icon className="size-3.5" />
                      <span className="sr-only">移除</span>
                    </Button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </AppPage>
  );
}

function showError(error: unknown) {
  toast.error(error instanceof Error ? error.message : "操作失败");
}
