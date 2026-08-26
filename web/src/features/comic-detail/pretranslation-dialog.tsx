import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircleIcon,
  CheckIcon,
  LoaderCircleIcon,
  SearchIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import { Dialog } from "radix-ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type {
  ChapterTranslationOverview,
  ComicTranslationOverview,
  TranslationBatchSummary,
} from "@/domain/api";
import {
  chapterIdsInRange,
  selectedChapterIds,
  selectionSummary,
  type PretranslationSelectionMode,
} from "@/features/comic-detail/pretranslation-selection";
import {
  PretranslationBatchCard,
  type PretranslationBatchAction,
} from "@/features/pretranslation/pretranslation-batch-card";
import { usePretranslationBatchActions } from "@/features/pretranslation/use-pretranslation-batch-actions";
import { api, ApiError } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

const overlayClass =
  "fixed inset-0 z-[70] bg-black/55 backdrop-blur-sm data-[state=closed]:animate-none";

const modeLabels: Array<[PretranslationSelectionMode, string]> = [
  ["all", "全部"],
  ["specific", "指定章节"],
  ["from", "从指定章开始"],
];

export function PretranslationDialog({
  open,
  onOpenChange,
  comicId,
  comicTitle,
  overview,
  loading,
  error,
  onRetry,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  comicId: string;
  comicTitle: string;
  overview: ComicTranslationOverview | undefined;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}) {
  const batch = overview?.batch ?? null;
  const previousBatchId = useRef<string | null>(null);

  useEffect(() => {
    if (batch) {
      previousBatchId.current = batch.batchId;
    } else if (open && previousBatchId.current && !loading) {
      previousBatchId.current = null;
      onOpenChange(false);
    }
  }, [batch, loading, onOpenChange, open]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={overlayClass} />
        <Dialog.Content className="fixed inset-0 z-[80] flex h-[100dvh] w-screen flex-col overflow-hidden bg-background outline-none sm:top-1/2 sm:left-1/2 sm:h-auto sm:max-h-[min(48rem,calc(100dvh-2rem))] sm:w-[min(44rem,calc(100vw-2rem))] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-3xl sm:border sm:shadow-2xl">
          <div className="flex shrink-0 items-start gap-3 border-b px-4 py-4 sm:px-6 sm:py-5">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <SparklesIcon className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="font-semibold">
                {batch ? "预先翻译进度" : "预先翻译"}
              </Dialog.Title>
              <Dialog.Description className="mt-1 truncate text-sm text-muted-foreground">
                {comicTitle}
              </Dialog.Description>
            </div>
            <Dialog.Close className="flex size-10 shrink-0 items-center justify-center rounded-full text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
              <XIcon className="size-5" />
              <span className="sr-only">关闭预先翻译窗口</span>
            </Dialog.Close>
          </div>

          {loading && !overview ? (
            <div className="flex min-h-0 flex-1 items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
              <LoaderCircleIcon className="size-4 animate-spin" /> 正在读取章节翻译状态…
            </div>
          ) : error && !overview ? (
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
              <AlertCircleIcon className="size-7 text-destructive" />
              <div>
                <p className="font-medium">章节翻译状态读取失败</p>
                <p className="mt-1 text-sm text-muted-foreground">{error.message}</p>
              </div>
              <Button variant="outline" onClick={onRetry}>
                重新加载
              </Button>
            </div>
          ) : batch ? (
            <BatchDetail batch={batch} />
          ) : overview ? (
            <BatchSelection
              open={open}
              comicId={comicId}
              overview={overview}
              onOpenChange={onOpenChange}
            />
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function BatchDetail({ batch }: { batch: TranslationBatchSummary }) {
  const actions = usePretranslationBatchActions();
  const pendingAction =
    actions.isPending && actions.variables?.batchId === batch.batchId
      ? actions.variables.action
      : null;
  const runAction = (action: PretranslationBatchAction) => {
    if (
      action === "cancel-pending" &&
      !window.confirm(
        "取消尚未开始的章节？\n\n当前章节会正常完成；如需立即停止，请到设置页使用单话的“强制停止”。",
      )
    ) {
      return;
    }
    if (
      action === "close" &&
      !window.confirm("结束这个批次？\n\n底层单话翻译记录会保留，之后仍可重新选择失败章节。")
    ) {
      return;
    }
    actions.mutate({ batchId: batch.batchId, action });
  };
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
      <PretranslationBatchCard batch={batch} pendingAction={pendingAction} onAction={runAction} />
    </div>
  );
}

function BatchSelection({
  open,
  comicId,
  overview,
  onOpenChange,
}: {
  open: boolean;
  comicId: string;
  overview: ComicTranslationOverview;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const chapters = overview.chapters;
  const chapterKey = chapters.map((chapter) => chapter.chapterId).join("\u0000");
  const oldestFirst = useMemo(
    () => [...chapters].sort((first, second) => first.position - second.position),
    [chapters],
  );
  const oldestId = oldestFirst[0]?.chapterId ?? "";
  const newestId = oldestFirst.at(-1)?.chapterId ?? "";
  const [mode, setMode] = useState<PretranslationSelectionMode>("all");
  const [specificIds, setSpecificIds] = useState<Set<string>>(() => new Set());
  const [fromChapterId, setFromChapterId] = useState(oldestId);
  const [rangeStart, setRangeStart] = useState(oldestId);
  const [rangeEnd, setRangeEnd] = useState(newestId);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!open) return;
    setMode("all");
    setSpecificIds(new Set());
    setFromChapterId(oldestId);
    setRangeStart(oldestId);
    setRangeEnd(newestId);
    setSearch("");
  }, [chapterKey, newestId, oldestId, open]);

  const selectedIds = selectedChapterIds(mode, chapters, specificIds, fromChapterId);
  const selectedSet = new Set(selectedIds);
  const summary = selectionSummary(chapters, selectedIds);
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const visibleChapters = normalizedSearch
    ? chapters.filter((chapter) =>
        chapter.chapterTitle.toLocaleLowerCase().includes(normalizedSearch),
      )
    : chapters;
  const options = oldestFirst.map((chapter) => [chapter.chapterId, chapter.chapterTitle] as const);

  const createBatch = useMutation({
    mutationFn: () => api.createTranslationBatch(comicId, selectedIds),
    onSuccess: (result) => {
      if (result.batch) {
        queryClient.setQueryData<ComicTranslationOverview>(
          queryKeys.translationOverview(comicId),
          (current) => (current ? { ...current, batch: result.batch } : current),
        );
      }
      if (result.noWork) {
        toast.success("所选章节都已完整翻译，无需处理");
      } else {
        toast.success(`已加入 ${result.workCount} 话，后台将从旧到新处理`);
      }
      onOpenChange(false);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.translationOverview(comicId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.backgroundTranslationBatches,
      });
    },
    onError: async (requestError) => {
      if (requestError instanceof ApiError && requestError.code === "TRANSLATION_BATCH_EXISTS") {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.translationOverview(comicId),
        });
        toast.info("已打开当前未结束的批次");
        return;
      }
      toast.error(requestError instanceof Error ? requestError.message : "批次创建失败");
    },
  });

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="space-y-5 p-4 sm:p-6">
          <div className="grid grid-cols-3 rounded-2xl bg-muted p-1" aria-label="章节选择方式">
            {modeLabels.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={mode === value}
                className={cn(
                  "min-h-10 rounded-xl px-2 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring sm:text-sm",
                  mode === value
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
                onClick={() => setMode(value)}
              >
                {label}
              </button>
            ))}
          </div>

          {mode === "all" && (
            <div className="rounded-2xl border bg-muted/30 p-4 text-sm text-muted-foreground">
              已选择当前目录中的全部 {chapters.length} 话。完整成功的章节会自动跳过。
            </div>
          )}

          {mode === "from" && (
            <div className="space-y-3 rounded-2xl border p-4">
              <p className="text-sm font-medium">起始章节</p>
              <Select
                value={fromChapterId}
                options={options}
                onValueChange={setFromChapterId}
                ariaLabel="选择预先翻译的起始章节"
              />
              <p className="text-xs text-muted-foreground">
                包含所选章节，以及目录中所有比它更新的章节。
              </p>
            </div>
          )}

          {mode === "specific" && (
            <>
              <div className="space-y-3 rounded-2xl border p-4">
                <p className="text-sm font-medium">选择连续区间</p>
                <div className="grid gap-2 sm:grid-cols-[1fr_auto_1fr_auto] sm:items-center">
                  <Select
                    value={rangeStart}
                    options={options}
                    onValueChange={setRangeStart}
                    ariaLabel="选择区间的第一个边界章节"
                  />
                  <span className="hidden text-xs text-muted-foreground sm:inline">至</span>
                  <Select
                    value={rangeEnd}
                    options={options}
                    onValueChange={setRangeEnd}
                    ariaLabel="选择区间的第二个边界章节"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      setSpecificIds(chapterIdsInRange(chapters, rangeStart, rangeEnd))
                    }
                  >
                    应用区间
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  应用后会替换当前选择，仍可在下方逐章调整。
                </p>
              </div>

              <div className="relative">
                <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索章节"
                  aria-label="搜索章节"
                  className="pl-9"
                />
              </div>

              <div className="divide-y overflow-hidden rounded-2xl border">
                {visibleChapters.length > 0 ? (
                  visibleChapters.map((chapter) => (
                    <ChapterCheckboxRow
                      key={chapter.chapterId}
                      chapter={chapter}
                      checked={selectedSet.has(chapter.chapterId)}
                      onChange={(checked) => {
                        setSpecificIds((current) => {
                          const next = new Set(current);
                          if (checked) next.add(chapter.chapterId);
                          else next.delete(chapter.chapterId);
                          return next;
                        });
                      }}
                    />
                  ))
                ) : (
                  <p className="p-6 text-center text-sm text-muted-foreground">没有匹配的章节</p>
                )}
              </div>
            </>
          )}

          {mode !== "specific" && (
            <div className="divide-y overflow-hidden rounded-2xl border">
              {chapters
                .filter((chapter) => selectedSet.has(chapter.chapterId))
                .map((chapter) => (
                  <ChapterStatusRow key={chapter.chapterId} chapter={chapter} />
                ))}
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t bg-background px-4 py-4 sm:px-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm">
            <p className="font-medium">
              已选 {summary.selectedCount} 话 · 实际补齐 {summary.workCount} 话
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {summary.skippedCount > 0
                ? `${summary.skippedCount} 话将跳过 · 后台按旧到新`
                : "后台按旧到新"}
              {summary.retryCount > 0 ? ` · ${summary.retryCount} 话将重试失败项` : ""}
              {" · 一次处理一章"}
            </p>
          </div>
          <Button
            size="lg"
            disabled={
              createBatch.isPending || summary.selectedCount === 0 || summary.workCount === 0
            }
            onClick={() => createBatch.mutate()}
          >
            {createBatch.isPending ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : summary.workCount === 0 && summary.selectedCount > 0 ? (
              <CheckIcon className="size-4" />
            ) : (
              <SparklesIcon className="size-4" />
            )}
            {createBatch.isPending
              ? "正在创建…"
              : summary.workCount === 0 && summary.selectedCount > 0
                ? "无需处理"
                : "开始预先翻译"}
          </Button>
        </div>
      </div>
    </>
  );
}

function ChapterCheckboxRow({
  chapter,
  checked,
  onChange,
}: {
  chapter: ChapterTranslationOverview;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex min-h-14 cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/60">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="size-4 shrink-0 accent-primary"
      />
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{chapter.chapterTitle}</span>
      <ChapterStateLabel chapter={chapter} />
    </label>
  );
}

function ChapterStatusRow({ chapter }: { chapter: ChapterTranslationOverview }) {
  return (
    <div className="flex min-h-12 items-center gap-3 px-4 py-3">
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{chapter.chapterTitle}</span>
      <ChapterStateLabel chapter={chapter} />
    </div>
  );
}

function ChapterStateLabel({ chapter }: { chapter: ChapterTranslationOverview }) {
  const labels: Record<ChapterTranslationOverview["status"], string> = {
    not_started: "未开始",
    active: "已有任务",
    paused: "将继续",
    completed: "将跳过",
    needs_retry: "将重试失败项",
    failed: "将继续",
  };
  return (
    <span
      className={cn(
        "shrink-0 text-xs text-muted-foreground",
        chapter.status === "completed" && "text-emerald-600 dark:text-emerald-400",
        (chapter.status === "needs_retry" || chapter.status === "failed") &&
          "text-amber-700 dark:text-amber-300",
      )}
    >
      {labels[chapter.status]}
    </span>
  );
}
