import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { HeartIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { AppPage } from "@/components/app-page";
import { ComicCard } from "@/components/comic-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export const Route = createFileRoute("/_app/favorites")({
  component: FavoritesPage,
});

function FavoritesPage() {
  const queryClient = useQueryClient();
  const favorites = useQuery({ queryKey: queryKeys.favorites, queryFn: api.favorites });
  const remove = useMutation({
    mutationFn: api.deleteFavorite,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.favorites });
      toast.success("已取消收藏");
    },
    onError: showError,
  });
  const clear = useMutation({
    mutationFn: api.clearFavorites,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.favorites });
      toast.success("收藏已清空");
    },
    onError: showError,
  });

  return (
    <AppPage>
      <header className="flex items-end justify-between gap-4">
        <div>
          <div className="mb-3 flex size-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <HeartIcon className="size-5" />
          </div>
          <h1 className="text-4xl font-bold tracking-tight">收藏</h1>
          <p className="mt-2 text-muted-foreground">跨设备保存在 ComicLens 服务器。</p>
        </div>
        {!!favorites.data?.length && (
          <Button
            variant="outline"
            disabled={clear.isPending}
            onClick={() => {
              if (window.confirm("确定清空全部收藏吗？")) clear.mutate();
            }}
          >
            <Trash2Icon className="size-4" /> 清空
          </Button>
        )}
      </header>

      {favorites.isPending ? (
        <LoadingState label="正在读取收藏…" />
      ) : favorites.isError ? (
        <ErrorState error={favorites.error} retry={() => void favorites.refetch()} />
      ) : favorites.data.length === 0 ? (
        <EmptyState title="还没有收藏" description="在 Comic 详情页点击收藏，内容会出现在这里。" />
      ) : (
        <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {favorites.data.map((item) => (
            <div key={item.comic.comicId} className="min-w-0">
              <ComicCard comic={item.comic} />
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 w-full text-muted-foreground"
                disabled={remove.isPending}
                onClick={() => remove.mutate(item.comic.comicId)}
              >
                <Trash2Icon className="size-3.5" /> 移除
              </Button>
            </div>
          ))}
        </div>
      )}
    </AppPage>
  );
}

function showError(error: unknown) {
  toast.error(error instanceof Error ? error.message : "操作失败");
}
