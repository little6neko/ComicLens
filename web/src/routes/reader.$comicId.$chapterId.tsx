import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  ArrowLeftIcon,
  BookOpenIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Columns2Icon,
  ImagesIcon,
  LanguagesIcon,
  LoaderCircleIcon,
  PanelTopIcon,
  RefreshCwIcon,
  RotateCwIcon,
  SettingsIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { toast } from "sonner";

import { ErrorState, LoadingState } from "@/components/query-state";
import { Button, buttonVariants } from "@/components/ui/button";
import type {
  ReaderPage,
  TranslationPageState,
  TranslationPageStatus,
  TranslationTaskState,
} from "@/domain/api";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";
import { positivePage } from "@/lib/route-search";
import { cn } from "@/lib/utils";

type ReadingMode = "strip" | "page" | "double";

const activeTaskStatuses = new Set(["queued", "running", "stopping_after_page"]);

const stageLabels: Record<TranslationPageStatus, string> = {
  idle: "等待翻译",
  pending: "等待翻译",
  downloading: "下载原图",
  ocr: "OCR 识别",
  translating: "翻译文本",
  rendering: "生成译图",
  completed: "翻译完成",
  failed: "翻译失败",
};

export const Route = createFileRoute("/reader/$comicId/$chapterId")({
  validateSearch: (search: Record<string, unknown>) => ({ page: positivePage(search.page) }),
  component: ReaderPageView,
});

function ReaderPageView() {
  const { comicId, chapterId } = Route.useParams();
  const { page: requestedPage } = Route.useSearch();
  const queryClient = useQueryClient();
  const taskKey = queryKeys.translation(comicId, chapterId);
  const chapterKey = `${comicId}:${chapterId}`;
  const [translationEnabled, setTranslationEnabled] = useState<boolean | null>(null);
  const [modeOverride, setModeOverride] = useState<ReadingMode | null>(null);
  const [currentPageIndex, setCurrentPageIndex] = useState(requestedPage - 1);
  const [actionError, setActionError] = useState<string | null>(null);
  const pageElements = useRef(new Map<number, HTMLElement>());
  const initializedChapter = useRef<string | null>(null);
  const initialScrollChapter = useRef<string | null>(null);
  const markedReadChapter = useRef<string | null>(null);

  const manifest = useQuery({
    queryKey: queryKeys.manifest(comicId, chapterId),
    queryFn: () => api.manifest(comicId, chapterId),
    ...queryTimes.manifest,
  });
  const comic = useQuery({
    queryKey: queryKeys.comic(comicId),
    queryFn: () => api.comic(comicId),
    ...queryTimes.detail,
  });
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: api.settings });
  const task = useQuery({
    queryKey: taskKey,
    queryFn: () => api.translation(comicId, chapterId),
    refetchInterval: (query) =>
      activeTaskStatuses.has(query.state.data?.status ?? "") ? 1000 : false,
  });

  const updateTask = (next: TranslationTaskState) => {
    queryClient.setQueryData(taskKey, next);
    setActionError(null);
  };
  const showActionError = (error: unknown) => {
    const message = error instanceof Error ? error.message : "翻译操作失败";
    setActionError(message);
    toast.error(message);
  };

  const startTranslation = useMutation({
    mutationFn: () => api.startTranslation(comicId, chapterId),
    onSuccess: (result) => updateTask(result.task),
    onError: showActionError,
  });
  const pauseTranslation = useMutation({
    mutationFn: () => api.pauseTranslation(comicId, chapterId),
    onSuccess: (result) => updateTask(result.task),
    onError: showActionError,
  });
  const retranslate = useMutation({
    mutationFn: () => api.retranslate(comicId, chapterId),
    onSuccess: (result) => {
      updateTask(result.task);
      toast.success("已开始重新翻译本话");
    },
    onError: showActionError,
  });
  const retryPage = useMutation({
    mutationFn: (pageIndex: number) => api.retryPage(comicId, chapterId, pageIndex),
    onSuccess: (result) => {
      updateTask(result.task);
      toast.success("已重新加入翻译队列");
    },
    onError: showActionError,
  });
  const saveMode = useMutation({
    mutationFn: (readingMode: ReadingMode) => api.patchSettings({ readingMode }),
    onSuccess: (next) => queryClient.setQueryData(queryKeys.settings, next),
    onError: (error) => toast.error(error instanceof Error ? error.message : "阅读模式保存失败"),
  });
  const markRead = useMutation({
    mutationFn: () => api.setChapterRead(comicId, chapterId),
    onSuccess: (next) => queryClient.setQueryData(queryKeys.readChapters(comicId), next),
  });

  const readingMode = modeOverride ?? settings.data?.readingMode ?? "strip";
  const pageDirection = settings.data?.pageDirection ?? "ltr";
  const effectivePages = useMemo(() => {
    if (!manifest.data) return [];
    const taskPages = new Map((task.data?.pages ?? []).map((page) => [page.pageIndex, page]));
    return manifest.data.pages.map((page) => mergePage(page, taskPages.get(page.index)));
  }, [manifest.data, task.data?.pages]);
  const totalPages = effectivePages.length;
  const clampedCurrent = clamp(currentPageIndex, 0, Math.max(0, totalPages - 1));
  const completionPageIndex = Math.max(0, totalPages - (readingMode === "double" ? 2 : 1));
  const activePage = task.data?.currentPageIndex;
  const taskProgress = task.data?.totalPages
    ? Math.round(((task.data.completedPages + task.data.failedPages) / task.data.totalPages) * 100)
    : 0;

  useEffect(() => {
    if (initializedChapter.current === chapterKey || !manifest.isSuccess) return;
    if (!settings.isSuccess && !settings.isError) return;
    initializedChapter.current = chapterKey;
    setCurrentPageIndex(requestedPage - 1);
    setModeOverride(null);
    setActionError(null);
    const enabled = settings.data?.realtimeTranslationDefault ?? false;
    setTranslationEnabled(enabled);
    if (enabled) startTranslation.mutate();
  }, [
    chapterKey,
    manifest.isSuccess,
    requestedPage,
    settings.data?.realtimeTranslationDefault,
    settings.isError,
    settings.isSuccess,
  ]);

  useEffect(() => {
    if (!totalPages || currentPageIndex === clampedCurrent) return;
    setCurrentPageIndex(clampedCurrent);
  }, [clampedCurrent, currentPageIndex, totalPages]);

  useEffect(() => {
    if (readingMode !== "strip" || !totalPages) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
        if (visible) setCurrentPageIndex(Number((visible.target as HTMLElement).dataset.page));
      },
      { rootMargin: "-42% 0px -48% 0px", threshold: [0, 0.01] },
    );
    for (const element of pageElements.current.values()) observer.observe(element);
    return () => observer.disconnect();
  }, [chapterKey, readingMode, totalPages]);

  useEffect(() => {
    if (
      readingMode !== "strip" ||
      !manifest.isSuccess ||
      initialScrollChapter.current === chapterKey
    ) {
      return;
    }
    initialScrollChapter.current = chapterKey;
    const pageIndex = clamp(requestedPage - 1, 0, Math.max(0, totalPages - 1));
    window.setTimeout(() => pageElements.current.get(pageIndex)?.scrollIntoView(), 0);
  }, [chapterKey, manifest.isSuccess, readingMode, requestedPage, totalPages]);

  useEffect(() => {
    if (!comic.data || !manifest.data || totalPages === 0) return;
    const timeout = window.setTimeout(() => {
      void api
        .saveHistory(comic.data, chapterId, manifest.data.title, clampedCurrent, totalPages)
        .then(() => queryClient.invalidateQueries({ queryKey: queryKeys.history }))
        .catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timeout);
  }, [chapterId, clampedCurrent, comic.data, manifest.data, queryClient, totalPages]);

  useEffect(() => {
    if (
      totalPages === 0 ||
      clampedCurrent < completionPageIndex ||
      markedReadChapter.current === chapterKey
    ) {
      return;
    }
    markedReadChapter.current = chapterKey;
    markRead.mutate();
  }, [chapterKey, clampedCurrent, completionPageIndex, totalPages]);

  useEffect(() => {
    if (readingMode === "strip") return;
    const move = (direction: -1 | 1) => {
      const amount = readingMode === "double" ? 2 : 1;
      setCurrentPageIndex((value) => clamp(value + direction * amount, 0, totalPages - 1));
    };
    const listener = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") move(pageDirection === "rtl" ? 1 : -1);
      if (event.key === "ArrowRight") move(pageDirection === "rtl" ? -1 : 1);
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [pageDirection, readingMode, totalPages]);

  function toggleTranslation() {
    const next = !translationEnabled;
    setTranslationEnabled(next);
    setActionError(null);
    if (next) startTranslation.mutate();
    else pauseTranslation.mutate();
  }

  function changeMode(mode: ReadingMode) {
    setModeOverride(mode);
    saveMode.mutate(mode);
  }

  function retranslateChapter() {
    if (!window.confirm("重新翻译本话会再次调用 OCR 和翻译接口，确定继续吗？")) return;
    setTranslationEnabled(true);
    retranslate.mutate();
  }

  function movePage(direction: -1 | 1) {
    const amount = readingMode === "double" ? 2 : 1;
    setCurrentPageIndex((value) => clamp(value + direction * amount, 0, totalPages - 1));
  }

  if (manifest.isPending) {
    return (
      <main className="min-h-dvh bg-zinc-950 text-zinc-100">
        <LoadingState label="正在取得章节图片…" />
      </main>
    );
  }
  if (manifest.isError) {
    return (
      <main className="min-h-dvh bg-zinc-950 px-4 py-20 text-zinc-100">
        <div className="mx-auto max-w-2xl">
          <ErrorState error={manifest.error} retry={() => void manifest.refetch()} />
        </div>
      </main>
    );
  }

  const currentChapterIndex = comic.data?.chapters.findIndex(
    (item) => item.chapterId === chapterId,
  );
  const newerChapter =
    currentChapterIndex !== undefined && currentChapterIndex > 0
      ? comic.data?.chapters[currentChapterIndex - 1]
      : undefined;
  const olderChapter =
    currentChapterIndex !== undefined && currentChapterIndex >= 0
      ? comic.data?.chapters[currentChapterIndex + 1]
      : undefined;
  const visiblePages = getVisiblePages(effectivePages, clampedCurrent, readingMode, pageDirection);

  return (
    <main className="min-h-dvh bg-zinc-950 text-zinc-100 selection:bg-white selection:text-black">
      <ReaderToolbar
        comicId={comicId}
        title={manifest.data.title}
        translationEnabled={translationEnabled ?? false}
        translationBusy={startTranslation.isPending || pauseTranslation.isPending}
        task={task.data}
        progress={taskProgress}
        readingMode={readingMode}
        onToggleTranslation={toggleTranslation}
        onMode={changeMode}
        onRetranslate={retranslateChapter}
        retranslating={retranslate.isPending}
      />

      <div className="h-28" />

      {actionError && (
        <div className="mx-auto mb-5 flex max-w-3xl items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
          <p className="min-w-0 flex-1">{actionError}。请检查服务器翻译设置。</p>
          <a href="/settings" className="shrink-0 underline underline-offset-4">
            设置
          </a>
          <button type="button" onClick={() => setActionError(null)} aria-label="关闭提示">
            <XIcon className="size-4" />
          </button>
        </div>
      )}

      {readingMode === "strip" ? (
        <section className="mx-auto flex max-w-[72rem] flex-col items-center pb-24">
          {effectivePages.map((page) => (
            <ReaderImage
              key={page.index}
              page={page}
              translationEnabled={translationEnabled ?? false}
              active={activePage === page.index}
              retrying={retryPage.isPending && retryPage.variables === page.index}
              onRetry={() => retryPage.mutate(page.index)}
              elementRef={(element) => {
                if (element) pageElements.current.set(page.index, element);
                else pageElements.current.delete(page.index);
              }}
            />
          ))}
        </section>
      ) : (
        <section className="flex min-h-[calc(100dvh-7rem)] flex-col px-3 pb-24 sm:px-6">
          <div
            className={cn(
              "mx-auto flex w-full max-w-[92rem] flex-1 items-center justify-center gap-1 sm:gap-3",
              readingMode === "page" && "max-w-[72rem]",
            )}
          >
            {visiblePages.map((page) => (
              <ReaderImage
                key={page.index}
                page={page}
                paged
                translationEnabled={translationEnabled ?? false}
                active={activePage === page.index}
                retrying={retryPage.isPending && retryPage.variables === page.index}
                onRetry={() => retryPage.mutate(page.index)}
              />
            ))}
          </div>
          <div className="fixed bottom-5 left-1/2 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full border border-white/10 bg-zinc-900/85 p-1.5 shadow-xl backdrop-blur-xl">
            <Button
              variant="ghost"
              size="icon"
              className="rounded-full text-zinc-100 hover:bg-white/10 hover:text-white"
              disabled={clampedCurrent === 0}
              onClick={() => movePage(-1)}
              aria-label="上一页"
            >
              <ChevronLeftIcon className="size-5" />
            </Button>
            <span className="min-w-20 text-center text-xs tabular-nums text-zinc-300">
              {clampedCurrent + 1} / {totalPages}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="rounded-full text-zinc-100 hover:bg-white/10 hover:text-white"
              disabled={clampedCurrent >= completionPageIndex}
              onClick={() => movePage(1)}
              aria-label="下一页"
            >
              <ChevronRightIcon className="size-5" />
            </Button>
          </div>
        </section>
      )}

      <footer className="border-t border-white/10 px-4 py-12">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
          {olderChapter ? (
            <a
              href={`/reader/${encodeURIComponent(comicId)}/${encodeURIComponent(olderChapter.chapterId)}`}
              className="min-w-0 rounded-2xl border border-white/10 px-4 py-3 hover:bg-white/5"
            >
              <span className="block text-xs text-zinc-500">较早章节</span>
              <span className="mt-1 block truncate text-sm">{olderChapter.title}</span>
            </a>
          ) : (
            <span />
          )}
          {newerChapter && (
            <a
              href={`/reader/${encodeURIComponent(comicId)}/${encodeURIComponent(newerChapter.chapterId)}`}
              className="min-w-0 rounded-2xl border border-white/10 px-4 py-3 text-right hover:bg-white/5"
            >
              <span className="block text-xs text-zinc-500">较新章节</span>
              <span className="mt-1 block truncate text-sm">{newerChapter.title}</span>
            </a>
          )}
        </div>
      </footer>
    </main>
  );
}

interface EffectivePage extends ReaderPage {
  effectiveTranslatedUrl: string | null;
  effectiveTranslatedPartUrls: string[];
  effectiveStatus: TranslationPageStatus;
  effectiveError: TranslationPageState["error"];
}

function mergePage(page: ReaderPage, taskPage: TranslationPageState | undefined): EffectivePage {
  return {
    ...page,
    width: taskPage?.width ?? page.width,
    height: taskPage?.height ?? page.height,
    effectiveTranslatedUrl: taskPage?.translatedUrl ?? page.translatedUrl,
    effectiveTranslatedPartUrls: taskPage?.translatedPartUrls.length
      ? taskPage.translatedPartUrls
      : page.translatedPartUrls,
    effectiveStatus: taskPage?.status ?? page.translationStatus,
    effectiveError: taskPage?.error ?? page.error,
  };
}

function ReaderImage({
  page,
  translationEnabled,
  active,
  retrying,
  onRetry,
  paged = false,
  elementRef,
}: {
  page: EffectivePage;
  translationEnabled: boolean;
  active: boolean;
  retrying: boolean;
  onRetry: () => void;
  paged?: boolean;
  elementRef?: (element: HTMLElement | null) => void;
}) {
  const showTranslated = translationEnabled && !!page.effectiveTranslatedUrl;
  const showTranslatedParts =
    showTranslated && !paged && page.effectiveTranslatedPartUrls.length > 0;
  const source = showTranslated
    ? (page.effectiveTranslatedUrl ?? page.originalUrl)
    : page.originalUrl;
  const ratio = page.width && page.height ? `${page.width} / ${page.height}` : undefined;
  const style = ratio ? ({ "--page-ratio": ratio } as CSSProperties) : undefined;

  return (
    <article
      ref={elementRef}
      data-page={page.index}
      style={style}
      className={cn(
        "relative w-full",
        paged
          ? "flex min-h-0 min-w-0 flex-1 flex-col items-center justify-center"
          : "max-w-[72rem]",
      )}
    >
      <div
        className={cn(
          "relative flex w-full items-center justify-center overflow-hidden bg-zinc-900",
          paged && "max-h-[calc(100dvh-10rem)] rounded-xl",
        )}
      >
        {showTranslatedParts ? (
          <div className="flex w-full flex-col">
            {page.effectiveTranslatedPartUrls.map((partUrl, partIndex) => (
              <img
                key={partIndex}
                src={partUrl}
                alt={`第 ${page.index + 1} 页译图分片 ${partIndex + 1}`}
                loading={page.index < 2 && partIndex === 0 ? "eager" : "lazy"}
                className="block h-auto w-full max-w-full object-contain"
              />
            ))}
          </div>
        ) : (
          <img
            src={source}
            alt={`第 ${page.index + 1} 页${showTranslated ? "译图" : "原图"}`}
            loading={page.index < 2 ? "eager" : "lazy"}
            className={cn(
              "block max-w-full object-contain",
              paged ? "max-h-[calc(100dvh-10rem)] w-auto" : "h-auto w-full",
            )}
          />
        )}
        {translationEnabled && active && page.effectiveStatus !== "completed" && (
          <span className="absolute top-3 right-3 flex items-center gap-2 rounded-full bg-black/70 px-3 py-1.5 text-xs text-white backdrop-blur">
            <LoaderCircleIcon className="size-3.5 animate-spin" />
            {stageLabels[page.effectiveStatus]}
          </span>
        )}
        {showTranslated && (
          <span className="absolute top-3 left-3 rounded-full bg-emerald-500/80 px-2.5 py-1 text-[10px] font-semibold text-white backdrop-blur">
            译图
          </span>
        )}
      </div>

      {translationEnabled && page.effectiveStatus === "failed" && page.effectiveError && (
        <div className={cn("w-full bg-zinc-900 px-4 py-3", paged && "mt-2 max-w-2xl rounded-xl")}>
          <div className="flex items-center gap-3">
            <TriangleAlertIcon className="size-4 shrink-0 text-amber-400" />
            <div className="min-w-0 flex-1 text-sm">
              <p className="truncate text-zinc-200">{page.effectiveError.message}</p>
              <p className="mt-0.5 font-mono text-[10px] text-zinc-500">
                {page.effectiveError.stage} · {page.effectiveError.code}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={retrying}
              onClick={onRetry}
              className="border-white/15 bg-transparent text-zinc-100 hover:bg-white/10 hover:text-white"
            >
              {retrying ? (
                <LoaderCircleIcon className="size-3.5 animate-spin" />
              ) : (
                <RotateCwIcon className="size-3.5" />
              )}
              重新翻译此图
            </Button>
          </div>
        </div>
      )}
    </article>
  );
}

function ReaderToolbar({
  comicId,
  title,
  translationEnabled,
  translationBusy,
  task,
  progress,
  readingMode,
  onToggleTranslation,
  onMode,
  onRetranslate,
  retranslating,
}: {
  comicId: string;
  title: string;
  translationEnabled: boolean;
  translationBusy: boolean;
  task: TranslationTaskState | undefined;
  progress: number;
  readingMode: ReadingMode;
  onToggleTranslation: () => void;
  onMode: (mode: ReadingMode) => void;
  onRetranslate: () => void;
  retranslating: boolean;
}) {
  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-white/10 bg-zinc-950/85 px-3 py-3 backdrop-blur-xl sm:px-5">
      <div className="mx-auto flex max-w-[96rem] items-center gap-2">
        <a
          href={`/comic/${encodeURIComponent(comicId)}`}
          className={cn(
            buttonVariants({ variant: "ghost", size: "icon" }),
            "shrink-0 rounded-full text-zinc-100 hover:bg-white/10 hover:text-white",
          )}
        >
          <ArrowLeftIcon className="size-5" />
          <span className="sr-only">返回 Comic 详情</span>
        </a>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{title}</p>
          <TaskSummary task={task} progress={progress} />
        </div>

        <div className="hidden items-center rounded-xl border border-white/10 p-1 md:flex">
          <ModeButton active={readingMode === "strip"} label="条漫" onClick={() => onMode("strip")}>
            <ImagesIcon className="size-4" />
          </ModeButton>
          <ModeButton active={readingMode === "page"} label="单页" onClick={() => onMode("page")}>
            <PanelTopIcon className="size-4" />
          </ModeButton>
          <ModeButton
            active={readingMode === "double"}
            label="双页"
            onClick={() => onMode("double")}
          >
            <Columns2Icon className="size-4" />
          </ModeButton>
        </div>
        <button
          type="button"
          onClick={() =>
            onMode(readingMode === "strip" ? "page" : readingMode === "page" ? "double" : "strip")
          }
          className="flex size-9 shrink-0 items-center justify-center rounded-full text-zinc-300 hover:bg-white/10 hover:text-white md:hidden"
          aria-label={`当前${modeLabel(readingMode)}，点击切换阅读模式`}
          title={`阅读模式：${modeLabel(readingMode)}`}
        >
          {readingMode === "strip" ? (
            <ImagesIcon className="size-4" />
          ) : readingMode === "page" ? (
            <PanelTopIcon className="size-4" />
          ) : (
            <Columns2Icon className="size-4" />
          )}
        </button>

        <Button
          variant={translationEnabled ? "default" : "outline"}
          onClick={onToggleTranslation}
          disabled={translationBusy}
          className={cn(
            "rounded-xl",
            translationEnabled
              ? "bg-white text-zinc-950 hover:bg-zinc-200"
              : "border-white/15 bg-transparent text-zinc-100 hover:bg-white/10 hover:text-white",
          )}
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
              translationEnabled ? "bg-emerald-500" : "bg-zinc-600",
            )}
          />
        </Button>

        <Button
          variant="ghost"
          size="icon"
          disabled={retranslating}
          onClick={onRetranslate}
          className="rounded-full text-zinc-100 hover:bg-white/10 hover:text-white"
          aria-label="重新翻译本话"
          title="重新翻译本话"
        >
          <RefreshCwIcon className={cn("size-4", retranslating && "animate-spin")} />
        </Button>
        <a
          href="/settings"
          className={cn(
            buttonVariants({ variant: "ghost", size: "icon" }),
            "hidden rounded-full text-zinc-100 hover:bg-white/10 hover:text-white sm:inline-flex",
          )}
        >
          <SettingsIcon className="size-4" />
          <span className="sr-only">翻译设置</span>
        </a>
      </div>
      {task && activeTaskStatuses.has(task.status) && (
        <div className="absolute inset-x-0 bottom-0 h-0.5 bg-white/10">
          <div className="h-full bg-white transition-[width]" style={{ width: `${progress}%` }} />
        </div>
      )}
    </header>
  );
}

function ModeButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs transition-colors",
        active ? "bg-white text-zinc-950" : "text-zinc-400 hover:text-white",
      )}
    >
      {children} {label}
    </button>
  );
}

function TaskSummary({
  task,
  progress,
}: {
  task: TranslationTaskState | undefined;
  progress: number;
}) {
  if (!task || task.status === "idle") {
    return (
      <p className="mt-0.5 flex items-center gap-1 text-xs text-zinc-500">
        <BookOpenIcon className="size-3" /> 原图可直接阅读
      </p>
    );
  }
  if (task.status === "stopping_after_page") {
    return (
      <p className="mt-0.5 text-xs text-amber-300">
        正在完成第 {(task.currentPageIndex ?? 0) + 1} 张后停止 · {progress}%
      </p>
    );
  }
  if (task.status === "queued" || task.status === "running") {
    return (
      <p className="mt-0.5 flex items-center gap-1.5 text-xs text-zinc-400">
        <LoaderCircleIcon className="size-3 animate-spin" />
        {task.completedPages}/{task.totalPages} 张 · {progress}%
      </p>
    );
  }
  if (task.status === "paused") {
    return <p className="mt-0.5 text-xs text-zinc-500">已在完整图片边界暂停</p>;
  }
  if (task.status === "completed") {
    return (
      <p className="mt-0.5 flex items-center gap-1 text-xs text-emerald-400">
        <CheckIcon className="size-3" /> 本话翻译完成
      </p>
    );
  }
  if (task.status === "completed_with_errors") {
    return <p className="mt-0.5 text-xs text-amber-300">翻译完成，{task.failedPages} 张失败</p>;
  }
  return <p className="mt-0.5 text-xs text-red-300">翻译任务失败</p>;
}

function getVisiblePages(
  pages: EffectivePage[],
  current: number,
  mode: ReadingMode,
  direction: "ltr" | "rtl",
) {
  if (mode === "page") return pages.slice(current, current + 1);
  const start = Math.floor(current / 2) * 2;
  const pair = pages.slice(start, start + 2);
  return direction === "rtl" ? pair.reverse() : pair;
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), maximum);
}

function modeLabel(mode: ReadingMode) {
  return { strip: "条漫", page: "单页", double: "双页" }[mode];
}
