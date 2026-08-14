import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircleIcon, LoaderCircleIcon, OctagonXIcon, ScanTextIcon } from "lucide-react";
import { useState, type Dispatch, type SetStateAction } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import type { BackgroundTranslationStage, BackgroundTranslationTask } from "@/domain/api";
import { api } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

const stageLabels: Record<BackgroundTranslationStage, string> = {
  preparing: "缓存与切分",
  queued: "等待开始",
  ocr: "OCR",
  translating: "翻译",
  rendering: "渲染",
  stopping: "正在停止",
  processing: "处理中",
};

interface TaskIdentity {
  comicId: string;
  chapterId: string;
}

export function BackgroundTranslationTasks() {
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState<Set<string>>(() => new Set());
  const tasks = useQuery({
    queryKey: queryKeys.backgroundTranslations,
    queryFn: api.backgroundTranslations,
    refetchInterval: 1000,
    refetchIntervalInBackground: true,
    placeholderData: (previous) => previous,
  });
  const forceStop = useMutation({
    mutationFn: ({ comicId, chapterId }: TaskIdentity) =>
      api.forceStopTranslation(comicId, chapterId),
    onMutate: (target) => updateStopping(setStopping, target, true),
    onSuccess: async (_result, target) => {
      queryClient.setQueryData<BackgroundTranslationTask[]>(
        queryKeys.backgroundTranslations,
        (current) =>
          current?.filter(
            (task) => task.comicId !== target.comicId || task.chapterId !== target.chapterId,
          ) ?? [],
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.backgroundTranslations }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.translation(target.comicId, target.chapterId),
        }),
      ]);
      toast.success("已强制停止，可从阅读器继续");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "强制停止失败");
    },
    onSettled: (_result, _error, target) => updateStopping(setStopping, target, false),
  });

  const taskItems = tasks.data ?? [];
  if (taskItems.length === 0 && !tasks.isError) return null;

  return (
    <section aria-labelledby="background-translation-heading">
      <div className="divide-y overflow-hidden rounded-3xl border bg-card shadow-sm">
        <div className="flex items-center gap-3 p-4 sm:p-5">
          <span className="flex size-10 items-center justify-center rounded-2xl bg-muted">
            <ScanTextIcon className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="background-translation-heading" className="font-semibold">
              后台 OCR 翻译
            </h2>
            <p className="text-xs text-muted-foreground">
              {taskItems.length > 0 ? `${taskItems.length} 话正在处理` : "任务状态暂时无法读取"}
            </p>
          </div>
          {tasks.isFetching && !tasks.isError && (
            <LoaderCircleIcon
              className="size-4 animate-spin text-muted-foreground"
              aria-label="刷新中"
            />
          )}
        </div>

        {tasks.isError && (
          <div className="flex items-center gap-2 bg-amber-500/10 px-4 py-3 text-sm text-amber-950 sm:px-5 dark:text-amber-100">
            <AlertCircleIcon className="size-4 shrink-0" />
            后台任务状态刷新失败，正在保留上一次结果。
          </div>
        )}

        {taskItems.map((task) => {
          const key = taskKey(task);
          return (
            <BackgroundTaskRow
              key={key}
              task={task}
              stopping={stopping.has(key)}
              onForceStop={() => {
                if (
                  window.confirm(
                    `强制停止「${task.comicTitle} · ${task.chapterTitle}」？\n\n这会立即中断本机 OCR、翻译和渲染，并保留检查点；PaddleOCR 云端任务可能仍会继续运行。之后可从阅读器继续。`,
                  )
                ) {
                  forceStop.mutate({ comicId: task.comicId, chapterId: task.chapterId });
                }
              }}
            />
          );
        })}
      </div>
    </section>
  );
}

function BackgroundTaskRow({
  task,
  stopping,
  onForceStop,
}: {
  task: BackgroundTranslationTask;
  stopping: boolean;
  onForceStop: () => void;
}) {
  const discovered = task.totalSegments > 0;
  const percentage = discovered
    ? Math.min(100, Math.round((task.completedSegments / task.totalSegments) * 100))
    : null;
  const readerUrl = `/reader/${encodeURIComponent(task.comicId)}/${encodeURIComponent(task.chapterId)}`;

  return (
    <article className="p-4 sm:p-5">
      <div className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
          <div className="min-w-0">
            <a
              href={`/comic/${encodeURIComponent(task.comicId)}`}
              className="line-clamp-1 font-semibold hover:underline"
            >
              {task.comicTitle}
            </a>
            <a
              href={readerUrl}
              className="mt-0.5 block truncate text-sm text-muted-foreground hover:text-foreground"
            >
              {task.chapterTitle}
            </a>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 sm:justify-end">
            <span
              className={cn(
                "shrink-0 rounded-full bg-muted px-2.5 py-1 text-[11px] font-medium text-muted-foreground",
                task.stage === "stopping" && "bg-amber-500/15 text-amber-700 dark:text-amber-300",
              )}
            >
              {stageLabels[task.stage]}
            </span>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={stopping}
              onClick={onForceStop}
            >
              {stopping ? (
                <LoaderCircleIcon className="size-3.5 animate-spin" />
              ) : (
                <OctagonXIcon className="size-3.5" />
              )}
              {stopping ? "停止中…" : "强制停止"}
            </Button>
          </div>
        </div>

        <div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            {percentage === null ? (
              <div className="h-full w-1/3 animate-pulse rounded-full bg-primary/70" />
            ) : (
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300"
                style={{ width: `${percentage}%` }}
              />
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {percentage === null ? (
              <span>正在发现分片</span>
            ) : (
              <span className="tabular-nums">
                {task.completedSegments} / {task.totalSegments} 个分片 · {percentage}%
              </span>
            )}
            <span className="shrink-0 tabular-nums">
              已缓存 {task.preparedPages} / {task.totalPages} 张源图
            </span>
            {task.failedSegments > 0 && (
              <span className="w-full text-destructive">{task.failedSegments} 个失败</span>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}

function taskKey(task: TaskIdentity): string {
  return `${task.comicId}\u0000${task.chapterId}`;
}

function updateStopping(
  setter: Dispatch<SetStateAction<Set<string>>>,
  target: TaskIdentity,
  active: boolean,
) {
  setter((current) => {
    const next = new Set(current);
    if (active) next.add(taskKey(target));
    else next.delete(taskKey(target));
    return next;
  });
}
