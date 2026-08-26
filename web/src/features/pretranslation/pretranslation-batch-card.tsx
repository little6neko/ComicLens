import {
  AlertCircleIcon,
  BanIcon,
  CirclePauseIcon,
  CirclePlayIcon,
  LoaderCircleIcon,
  OctagonXIcon,
  RotateCwIcon,
  XCircleIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { TranslationBatchStatus, TranslationBatchSummary } from "@/domain/api";
import { cn } from "@/lib/utils";

export type PretranslationBatchAction =
  | "pause"
  | "resume"
  | "cancel-pending"
  | "retry-failed"
  | "close";

const batchStatusLabels: Record<TranslationBatchStatus, string> = {
  queued: "等待批量槽",
  running: "正在预先翻译",
  pausing: "完成本章后暂停",
  paused: "已暂停",
  cancelling: "正在完成当前章",
  completed: "已完成",
  completed_with_errors: "有失败章节",
  cancelled: "已结束",
  failed: "批次需要处理",
};

export function PretranslationBatchCard({
  batch,
  pendingAction,
  onAction,
  forceStoppingCurrent = false,
  onForceStopCurrent,
  className,
}: {
  batch: TranslationBatchSummary;
  pendingAction: PretranslationBatchAction | null;
  onAction: (action: PretranslationBatchAction) => void;
  forceStoppingCurrent?: boolean;
  onForceStopCurrent?: () => void;
  className?: string;
}) {
  const resolved =
    batch.completedChapters +
    batch.skippedChapters +
    batch.failedChapters +
    batch.cancelledChapters;
  const percentage =
    batch.totalChapters > 0 ? Math.round((resolved / batch.totalChapters) * 100) : 0;
  const active = ["queued", "running", "pausing", "cancelling"].includes(batch.status);
  const current = batch.currentItem;
  const task = batch.currentTask;
  const taskUsesSegments = Boolean(task && (task.planningComplete || task.totalSegments > 0));
  const taskTotal = task ? (taskUsesSegments ? task.totalSegments : task.totalPages) : 0;
  const taskCompleted = task
    ? taskUsesSegments
      ? task.completedSegments
      : task.completedPages
    : 0;
  const taskFailed = task ? (taskUsesSegments ? task.failedSegments : task.failedPages) : 0;
  const taskStopping =
    task?.status === "stopping_after_page" || task?.status === "stopping_after_segment";
  const taskActive = Boolean(
    task &&
    ["preparing", "queued", "running", "stopping_after_page", "stopping_after_segment"].includes(
      task.status,
    ),
  );

  return (
    <article className={cn("overflow-hidden rounded-3xl border bg-card", className)}>
      <div className="space-y-5 p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <a
              href={`/comic/${encodeURIComponent(batch.comicId)}`}
              className="block truncate font-semibold hover:underline"
            >
              {batch.comicTitle}
            </a>
            <p className="mt-1 text-sm text-muted-foreground">
              {resolved} / {batch.totalChapters} 话已处理
            </p>
          </div>
          <span
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs font-medium text-muted-foreground",
              batch.status === "completed_with_errors" || batch.status === "failed"
                ? "bg-destructive/10 text-destructive"
                : batch.status === "paused"
                  ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                  : undefined,
            )}
          >
            {active && <LoaderCircleIcon className="size-3 animate-spin" />}
            {batchStatusLabels[batch.status]}
          </span>
        </div>

        <div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-300"
              style={{ width: `${percentage}%` }}
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="tabular-nums">{percentage}%</span>
            <span>完成 {batch.completedChapters}</span>
            {batch.skippedChapters > 0 && <span>跳过 {batch.skippedChapters}</span>}
            {batch.failedChapters > 0 && (
              <span className="text-destructive">失败 {batch.failedChapters}</span>
            )}
            {batch.cancelledChapters > 0 && <span>取消 {batch.cancelledChapters}</span>}
          </div>
        </div>

        {current && (
          <div className="rounded-2xl bg-muted/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground">当前章节</p>
                <a
                  href={`/reader/${encodeURIComponent(batch.comicId)}/${encodeURIComponent(current.chapterId)}`}
                  className="mt-0.5 block truncate text-sm font-medium hover:underline"
                >
                  {current.chapterTitle}
                </a>
              </div>
              {batch.interactiveYielded && (
                <span className="rounded-full bg-background px-2.5 py-1 text-[11px] text-muted-foreground">
                  正在让位给阅读器任务
                </span>
              )}
            </div>
            {task && (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                {taskTotal > 0 ? (
                  <span className="tabular-nums">
                    {taskCompleted} / {taskTotal} {taskUsesSegments ? "个分片" : "张图片"}
                    {taskFailed > 0 ? ` · ${taskFailed} 个失败` : ""}
                  </span>
                ) : (
                  <span>正在准备源图与分片</span>
                )}
                {onForceStopCurrent && taskActive && (
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    disabled={pendingAction !== null || forceStoppingCurrent || taskStopping}
                    onClick={onForceStopCurrent}
                  >
                    {forceStoppingCurrent || taskStopping ? (
                      <LoaderCircleIcon className="size-3.5 animate-spin" />
                    ) : (
                      <OctagonXIcon className="size-3.5" />
                    )}
                    {forceStoppingCurrent || taskStopping ? "停止中…" : "强制停止"}
                  </Button>
                )}
              </div>
            )}
          </div>
        )}

        {(batch.errorSummary || batch.pauseReason === "config") && (
          <div className="flex gap-2 rounded-2xl bg-amber-500/10 p-3 text-sm text-amber-950 dark:text-amber-100">
            <AlertCircleIcon className="mt-0.5 size-4 shrink-0" />
            <span>{batch.errorSummary || "当前服务配置不可用，请修改设置后继续。"}</span>
          </div>
        )}

        <BatchActions batch={batch} pendingAction={pendingAction} onAction={onAction} />
      </div>
    </article>
  );
}

function BatchActions({
  batch,
  pendingAction,
  onAction,
}: {
  batch: TranslationBatchSummary;
  pendingAction: PretranslationBatchAction | null;
  onAction: (action: PretranslationBatchAction) => void;
}) {
  const disabled = pendingAction !== null;
  const actionLabel = (action: PretranslationBatchAction, label: string) =>
    pendingAction === action ? "提交中…" : label;
  return (
    <div className="flex flex-wrap gap-2">
      {(batch.status === "queued" || batch.status === "running") && (
        <Button variant="outline" disabled={disabled} onClick={() => onAction("pause")}>
          {pendingAction === "pause" ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <CirclePauseIcon className="size-4" />
          )}
          {actionLabel("pause", batch.status === "running" ? "完成本章后暂停" : "暂停")}
        </Button>
      )}
      {batch.status === "paused" && (
        <Button disabled={disabled} onClick={() => onAction("resume")}>
          {pendingAction === "resume" ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <CirclePlayIcon className="size-4" />
          )}
          {actionLabel("resume", "继续")}
        </Button>
      )}
      {batch.status === "failed" && (
        <Button disabled={disabled} onClick={() => onAction("resume")}>
          {pendingAction === "resume" ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <CirclePlayIcon className="size-4" />
          )}
          {actionLabel("resume", "重新尝试")}
        </Button>
      )}
      {batch.status === "completed_with_errors" && (
        <Button disabled={disabled} onClick={() => onAction("retry-failed")}>
          {pendingAction === "retry-failed" ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <RotateCwIcon className="size-4" />
          )}
          {actionLabel("retry-failed", `重试失败章节 (${batch.failedChapters})`)}
        </Button>
      )}
      {["queued", "running", "pausing", "paused", "cancelling"].includes(batch.status) && (
        <Button
          variant="outline"
          disabled={disabled || batch.status === "cancelling"}
          onClick={() => onAction("cancel-pending")}
        >
          {pendingAction === "cancel-pending" || batch.status === "cancelling" ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <BanIcon className="size-4" />
          )}
          {batch.status === "cancelling"
            ? "正在完成当前章…"
            : actionLabel("cancel-pending", "取消剩余")}
        </Button>
      )}
      {(batch.status === "completed_with_errors" || batch.status === "failed") && (
        <Button variant="outline" disabled={disabled} onClick={() => onAction("close")}>
          {pendingAction === "close" ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <XCircleIcon className="size-4" />
          )}
          {actionLabel("close", "结束批次")}
        </Button>
      )}
    </div>
  );
}

export function pretranslationBatchButtonLabel(batch: TranslationBatchSummary | null) {
  if (!batch) return "预先翻译";
  if (batch.status === "paused") return "预先翻译已暂停";
  if (batch.status === "completed_with_errors" || batch.status === "failed") {
    return "预先翻译有失败项";
  }
  const resolved =
    batch.completedChapters +
    batch.skippedChapters +
    batch.failedChapters +
    batch.cancelledChapters;
  return `预先翻译 ${resolved} / ${batch.totalChapters}`;
}
