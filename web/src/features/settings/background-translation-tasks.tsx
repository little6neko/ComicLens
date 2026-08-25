import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircleIcon,
  LoaderCircleIcon,
  OctagonXIcon,
  RefreshCwIcon,
  RotateCwIcon,
  ScanTextIcon,
} from "lucide-react";
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
  needs_retry: "等待重试",
};

const activeTaskStatuses = new Set<BackgroundTranslationTask["status"]>([
  "preparing",
  "queued",
  "running",
  "stopping_after_page",
  "stopping_after_segment",
]);

interface TaskIdentity {
  comicId: string;
  chapterId: string;
}

export function BackgroundTranslationTasks() {
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState<Set<string>>(() => new Set());
  const [retryingFailed, setRetryingFailed] = useState<Set<string>>(() => new Set());
  const [retranslating, setRetranslating] = useState<Set<string>>(() => new Set());
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
    onMutate: (target) => updatePending(setStopping, target, true),
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
    onSettled: (_result, _error, target) => updatePending(setStopping, target, false),
  });
  const retryFailed = useMutation({
    mutationFn: ({ comicId, chapterId }: TaskIdentity) =>
      api.retryFailedTranslation(comicId, chapterId),
    onMutate: (target) => updatePending(setRetryingFailed, target, true),
    onSuccess: async (result, target) => {
      queryClient.setQueryData(
        queryKeys.translation(target.comicId, target.chapterId),
        result.task,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.backgroundTranslations }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.translation(target.comicId, target.chapterId),
        }),
      ]);
      toast.success(
        result.retriedCount > 0
          ? `已加入 ${result.retriedCount} 个失败项`
          : "当前没有需要重试的失败项",
      );
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "重试失败项失败");
    },
    onSettled: (_result, _error, target) => updatePending(setRetryingFailed, target, false),
  });
  const retranslate = useMutation({
    mutationFn: ({ comicId, chapterId }: TaskIdentity) => api.retranslate(comicId, chapterId),
    onMutate: (target) => updatePending(setRetranslating, target, true),
    onSuccess: async (result, target) => {
      queryClient.setQueryData(
        queryKeys.translation(target.comicId, target.chapterId),
        result.task,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.backgroundTranslations }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.translation(target.comicId, target.chapterId),
        }),
      ]);
      toast.success("已开始重新翻译本话");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "全部重试失败");
    },
    onSettled: (_result, _error, target) => updatePending(setRetranslating, target, false),
  });

  const taskItems = tasks.data ?? [];
  const activeCount = taskItems.filter((task) => activeTaskStatuses.has(task.status)).length;
  const waitingCount = taskItems.length - activeCount;
  const taskSummary = [
    activeCount > 0 ? `${activeCount} 话正在处理` : null,
    waitingCount > 0 ? `${waitingCount} 话等待重试` : null,
  ]
    .filter(Boolean)
    .join(" · ");
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
            <p className="text-xs text-muted-foreground">{taskSummary || "任务状态暂时无法读取"}</p>
          </div>
          {activeCount > 0 && !tasks.isError && (
            <LoaderCircleIcon
              className="size-4 animate-spin text-muted-foreground"
              aria-label="后台任务处理中"
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
              retryingFailed={retryingFailed.has(key)}
              retranslating={retranslating.has(key)}
              onRetryFailed={() => {
                retryFailed.mutate({ comicId: task.comicId, chapterId: task.chapterId });
              }}
              onRetranslate={() => {
                if (
                  window.confirm(
                    `全部重试「${task.comicTitle} · ${task.chapterTitle}」？\n\n这会再次调用 OCR 和翻译接口，已经成功的项目也会重新处理。`,
                  )
                ) {
                  retranslate.mutate({ comicId: task.comicId, chapterId: task.chapterId });
                }
              }}
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
  retryingFailed,
  retranslating,
  onRetryFailed,
  onRetranslate,
  onForceStop,
}: {
  task: BackgroundTranslationTask;
  stopping: boolean;
  retryingFailed: boolean;
  retranslating: boolean;
  onRetryFailed: () => void;
  onRetranslate: () => void;
  onForceStop: () => void;
}) {
  const usesSegments = task.totalSegments > 0 || task.planningComplete;
  const totalItems = usesSegments ? task.totalSegments : task.totalPages;
  const completedItems = usesSegments ? task.completedSegments : task.completedPages;
  const failedItems = usesSegments ? task.failedSegments : task.failedPages;
  const percentage =
    totalItems > 0 ? Math.min(100, Math.round((completedItems / totalItems) * 100)) : null;
  const active = activeTaskStatuses.has(task.status);
  const stoppingStatus =
    task.status === "stopping_after_page" || task.status === "stopping_after_segment";
  const actionPending = stopping || retryingFailed || retranslating;
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
                task.stage === "needs_retry" &&
                  "bg-amber-500/15 text-amber-700 dark:text-amber-300",
              )}
            >
              {stageLabels[task.stage]}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={actionPending || stoppingStatus}
              onClick={onRetranslate}
            >
              {retranslating ? (
                <LoaderCircleIcon className="size-3.5 animate-spin" />
              ) : (
                <RefreshCwIcon className="size-3.5" />
              )}
              {retranslating ? "提交中…" : "全部重试"}
            </Button>
            {active && (
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={actionPending || stoppingStatus}
                onClick={onForceStop}
              >
                {stopping || stoppingStatus ? (
                  <LoaderCircleIcon className="size-3.5 animate-spin" />
                ) : (
                  <OctagonXIcon className="size-3.5" />
                )}
                {stopping || stoppingStatus ? "停止中…" : "强制停止"}
              </Button>
            )}
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
                {completedItems} / {totalItems} {usesSegments ? "个分片" : "张图片"} · {percentage}%
              </span>
            )}
            <span className="shrink-0 tabular-nums">
              已缓存 {task.preparedPages} / {task.totalPages} 张源图
            </span>
            {failedItems > 0 && (
              <span className="flex w-full items-center gap-2 text-destructive">
                <span className="tabular-nums">{failedItems} 个失败</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 border-destructive/30 px-2.5 text-destructive hover:bg-destructive/10 hover:text-destructive"
                  disabled={actionPending || stoppingStatus}
                  onClick={onRetryFailed}
                >
                  {retryingFailed ? (
                    <LoaderCircleIcon className="size-3.5 animate-spin" />
                  ) : (
                    <RotateCwIcon className="size-3.5" />
                  )}
                  {retryingFailed ? "提交中…" : "重试失败"}
                </Button>
              </span>
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

function updatePending(
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
