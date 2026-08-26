import type { ReactNode } from "react";

import type { BackgroundTranslationStage, TranslationTaskProgress } from "@/domain/api";
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

export function TranslationTaskStageBadge({
  stage,
  className,
}: {
  stage: BackgroundTranslationStage;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-full bg-muted px-2.5 py-1 text-[11px] font-medium text-muted-foreground",
        (stage === "stopping" || stage === "needs_retry") &&
          "bg-amber-500/15 text-amber-700 dark:text-amber-300",
        className,
      )}
    >
      {stageLabels[stage]}
    </span>
  );
}

export function TranslationTaskProgressDetails({
  task,
  failureAction,
  className,
}: {
  task: TranslationTaskProgress | null;
  failureAction?: ReactNode;
  className?: string;
}) {
  const usesSegments = Boolean(task && (task.totalSegments > 0 || task.planningComplete));
  const totalItems = task ? (usesSegments ? task.totalSegments : task.totalPages) : 0;
  const completedItems = task ? (usesSegments ? task.completedSegments : task.completedPages) : 0;
  const failedItems = task ? (usesSegments ? task.failedSegments : task.failedPages) : 0;
  const percentage =
    totalItems > 0 ? Math.min(100, Math.round((completedItems / totalItems) * 100)) : null;

  return (
    <div className={className}>
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
        {task && (
          <span className="shrink-0 tabular-nums">
            已缓存 {task.preparedPages} / {task.totalPages} 张源图
          </span>
        )}
        {failedItems > 0 && (
          <span className="flex w-full items-center gap-2 text-destructive">
            <span className="tabular-nums">{failedItems} 个失败</span>
            {failureAction}
          </span>
        )}
      </div>
    </div>
  );
}
