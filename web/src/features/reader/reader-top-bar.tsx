import {
  ArrowLeftIcon,
  CheckIcon,
  LanguagesIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { TranslationTaskState } from "@/domain/api";

const activeStatuses = new Set([
  "preparing",
  "queued",
  "running",
  "stopping_after_page",
  "stopping_after_segment",
]);

export function ReaderTopBar({
  visible,
  comicId,
  comicTitle,
  chapterTitle,
  translationEnabled,
  translationBusy,
  retranslating,
  task,
  onToggleTranslation,
  onRetranslate,
}: {
  visible: boolean;
  comicId: string;
  comicTitle: string;
  chapterTitle: string;
  translationEnabled: boolean;
  translationBusy: boolean;
  retranslating: boolean;
  task: TranslationTaskState | undefined;
  onToggleTranslation: () => void;
  onRetranslate: () => void;
}) {
  const total = task?.totalSegments ?? task?.totalPages ?? 0;
  const completed = task?.completedSegments ?? task?.completedPages ?? 0;
  const progress = total > 0 ? Math.min(100, (completed / total) * 100) : 0;

  return (
    <header
      className={cn(
        "fixed top-3 left-1/2 z-50 w-[min(56rem,calc(100vw-1rem))] -translate-x-1/2 overflow-hidden rounded-[1.7rem] border border-white/10 bg-zinc-950/82 text-zinc-100 shadow-2xl shadow-black/30 backdrop-blur-2xl transition-all duration-200 sm:top-4",
        visible ? "translate-y-0 opacity-100" : "pointer-events-none -translate-y-5 opacity-0",
      )}
      onClick={(event) => event.stopPropagation()}
      onTouchMove={(event) => event.stopPropagation()}
    >
      <div className="flex min-h-16 items-center gap-2 px-2.5 py-2 sm:gap-3 sm:px-3">
        <a
          href={`/comic/${encodeURIComponent(comicId)}`}
          className="flex size-11 shrink-0 items-center justify-center rounded-full text-zinc-200 transition-colors hover:bg-white/10 hover:text-white"
          aria-label="返回 Comic 详情"
        >
          <ArrowLeftIcon className="size-5" />
        </a>

        <div className="min-w-0 flex-1 text-center sm:text-left">
          <p className="truncate text-sm font-semibold sm:text-base">{comicTitle || "ComicLens"}</p>
          <p className="mt-0.5 truncate text-[11px] text-zinc-400 sm:text-xs">{chapterTitle}</p>
          <TaskSummary task={task} />
        </div>

        <button
          type="button"
          className={cn(
            "flex h-10 shrink-0 items-center gap-2 rounded-full border px-3 text-xs font-medium transition-colors sm:px-4",
            translationEnabled
              ? "border-white bg-white text-zinc-950 hover:bg-zinc-200"
              : "border-white/15 bg-white/5 text-zinc-200 hover:bg-white/10",
          )}
          onClick={onToggleTranslation}
          aria-pressed={translationEnabled}
        >
          {translationBusy ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <LanguagesIcon className="size-4" />
          )}
          <span className="hidden sm:inline">实时翻译</span>
          <span
            className={cn(
              "size-2 rounded-full",
              translationEnabled ? "bg-emerald-500" : "bg-zinc-500",
            )}
          />
        </button>

        <button
          type="button"
          className="flex size-10 shrink-0 items-center justify-center rounded-full text-zinc-300 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-45"
          disabled={retranslating}
          onClick={onRetranslate}
          aria-label="重新翻译本话"
          title="重新翻译本话"
        >
          <RefreshCwIcon className={cn("size-4", retranslating && "animate-spin")} />
        </button>
      </div>

      {task && activeStatuses.has(task.status) && task.planningComplete && total > 0 && (
        <div className="h-0.5 bg-white/10">
          <div
            className="h-full bg-white transition-[width] duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </header>
  );
}

function TaskSummary({ task }: { task: TranslationTaskState | undefined }) {
  if (!task || task.status === "idle") {
    return <p className="mt-1 text-[10px] text-zinc-500 sm:text-[11px]">原图可直接阅读</p>;
  }
  if (task.status === "preparing") {
    return <StatusLine loading>正在准备本话</StatusLine>;
  }

  const total = task.totalSegments ?? task.totalPages;
  const completed = task.completedSegments ?? task.completedPages;
  const failed = task.failedSegments ?? task.failedPages;
  const progress =
    task.planningComplete || total > 0
      ? `${completed} / ${total}${failed ? ` · ${failed} 失败` : ""}`
      : "正在准备本话";

  if (task.status === "queued" || task.status === "running") {
    return <StatusLine loading>{progress}</StatusLine>;
  }
  if (task.status === "stopping_after_segment" || task.status === "stopping_after_page") {
    return (
      <p className="mt-1 text-[10px] text-amber-300 sm:text-[11px]">{progress} · 完成本片后暂停</p>
    );
  }
  if (task.status === "paused") {
    return <p className="mt-1 text-[10px] text-zinc-400 sm:text-[11px]">{progress} · 已暂停</p>;
  }
  if (task.status === "completed") {
    return (
      <p className="mt-1 flex items-center justify-center gap-1 text-[10px] text-emerald-400 sm:justify-start sm:text-[11px]">
        <CheckIcon className="size-3" /> {completed} / {total} · 已完成
      </p>
    );
  }
  if (task.status === "completed_with_errors") {
    return <p className="mt-1 text-[10px] text-amber-300 sm:text-[11px]">{progress}</p>;
  }
  return <p className="mt-1 text-[10px] text-red-300 sm:text-[11px]">翻译任务失败</p>;
}

function StatusLine({ loading, children }: { loading?: boolean; children: string }) {
  return (
    <p className="mt-1 flex items-center justify-center gap-1.5 text-[10px] text-zinc-400 sm:justify-start sm:text-[11px]">
      {loading && <LoaderCircleIcon className="size-3 animate-spin" />}
      <span className="tabular-nums">{children}</span>
    </p>
  );
}
