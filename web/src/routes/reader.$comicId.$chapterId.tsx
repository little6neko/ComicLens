import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { TriangleAlertIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { ErrorState, LoadingState } from "@/components/query-state";
import type { ReaderPage, TranslationPageState, TranslationTaskState } from "@/domain/api";
import { ReaderBottomBar } from "@/features/reader/reader-bottom-bar";
import { ReaderPageImage } from "@/features/reader/reader-page-image";
import { ReaderChapterDirectory, ReaderSettingsPanel } from "@/features/reader/reader-panels";
import { ReaderTopBar } from "@/features/reader/reader-top-bar";
import type { EffectiveReaderPage, ReadingMode } from "@/features/reader/types";
import { useReaderChrome } from "@/features/reader/use-reader-chrome";
import { api } from "@/lib/api-client";
import { queryKeys, queryTimes } from "@/lib/query-keys";
import { positivePage } from "@/lib/route-search";
import { cn } from "@/lib/utils";

const activeTaskStatuses = new Set([
  "preparing",
  "queued",
  "running",
  "stopping_after_page",
  "stopping_after_segment",
]);

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
  const [directionOverride, setDirectionOverride] = useState<"ltr" | "rtl" | null>(null);
  const [currentPageIndex, setCurrentPageIndex] = useState(requestedPage - 1);
  const [actionError, setActionError] = useState<string | null>(null);
  const [directoryOpen, setDirectoryOpen] = useState(false);
  const [readerSettingsOpen, setReaderSettingsOpen] = useState(false);
  const pageElements = useRef(new Map<number, HTMLElement>());
  const initializedChapter = useRef<string | null>(null);
  const initialScrollChapter = useRef<string | null>(null);
  const markedReadChapter = useRef<string | null>(null);
  const heldOpen = directoryOpen || readerSettingsOpen;

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
  const chrome = useReaderChrome(
    `${chapterKey}:${manifest.isSuccess ? "ready" : "loading"}`,
    heldOpen,
  );

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
  const retrySegment = useMutation({
    mutationFn: ({ pageIndex, segmentIndex }: { pageIndex: number; segmentIndex: number }) =>
      api.retrySegment(comicId, chapterId, pageIndex, segmentIndex),
    onSuccess: (result) => {
      updateTask(result.task);
      toast.success("此分片已重新加入翻译队列");
    },
    onError: showActionError,
  });
  const saveReadingSettings = useMutation({
    mutationFn: (patch: { readingMode?: ReadingMode; pageDirection?: "ltr" | "rtl" }) =>
      api.patchSettings(patch),
    onSuccess: (next) => queryClient.setQueryData(queryKeys.settings, next),
    onError: (error) => toast.error(error instanceof Error ? error.message : "阅读设置保存失败"),
  });
  const markRead = useMutation({
    mutationFn: () => api.setChapterRead(comicId, chapterId),
    onSuccess: (next) => queryClient.setQueryData(queryKeys.readChapters(comicId), next),
  });

  const readingMode = modeOverride ?? settings.data?.readingMode ?? "strip";
  const pageDirection = directionOverride ?? settings.data?.pageDirection ?? "ltr";
  const effectivePages = useMemo(() => {
    if (!manifest.data) return [];
    const taskPages = new Map((task.data?.pages ?? []).map((page) => [page.pageIndex, page]));
    return manifest.data.pages.map((page) => mergePage(page, taskPages.get(page.index)));
  }, [manifest.data, task.data?.pages]);
  const totalPages = effectivePages.length;
  const rawCurrent = clamp(currentPageIndex, 0, Math.max(0, totalPages - 1));
  const clampedCurrent = readingMode === "double" ? Math.floor(rawCurrent / 2) * 2 : rawCurrent;
  const completionPageIndex =
    readingMode === "double"
      ? Math.floor(Math.max(0, totalPages - 1) / 2) * 2
      : Math.max(0, totalPages - 1);

  useEffect(() => {
    if (initializedChapter.current === chapterKey || !manifest.isSuccess) return;
    if (!settings.isSuccess && !settings.isError) return;
    initializedChapter.current = chapterKey;
    setCurrentPageIndex(requestedPage - 1);
    setModeOverride(null);
    setDirectionOverride(null);
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
      { rootMargin: "-36% 0px -46% 0px", threshold: [0, 0.01] },
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
    const listener = (event: KeyboardEvent) => {
      const amount = readingMode === "double" ? 2 : 1;
      if (event.key === "ArrowLeft") {
        setCurrentPageIndex((value) =>
          clamp(value + (pageDirection === "rtl" ? amount : -amount), 0, totalPages - 1),
        );
      }
      if (event.key === "ArrowRight") {
        setCurrentPageIndex((value) =>
          clamp(value + (pageDirection === "rtl" ? -amount : amount), 0, totalPages - 1),
        );
      }
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
    saveReadingSettings.mutate({ readingMode: mode });
    if (mode === "strip") {
      window.setTimeout(() => pageElements.current.get(clampedCurrent)?.scrollIntoView(), 0);
    }
  }

  function changeDirection(direction: "ltr" | "rtl") {
    setDirectionOverride(direction);
    saveReadingSettings.mutate({ pageDirection: direction });
  }

  function retranslateChapter() {
    if (!window.confirm("重新翻译本话会再次调用 OCR 和翻译接口，确定继续吗？")) return;
    setTranslationEnabled(true);
    retranslate.mutate();
  }

  function jumpToPage(index: number) {
    const selected = clamp(index, 0, Math.max(0, totalPages - 1));
    const target = readingMode === "double" ? Math.floor(selected / 2) * 2 : selected;
    setCurrentPageIndex(target);
    if (readingMode === "strip") pageElements.current.get(target)?.scrollIntoView();
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
  const nextChapter =
    currentChapterIndex !== undefined && currentChapterIndex > 0
      ? comic.data?.chapters[currentChapterIndex - 1]
      : undefined;
  const previousChapter =
    currentChapterIndex !== undefined && currentChapterIndex >= 0
      ? comic.data?.chapters[currentChapterIndex + 1]
      : undefined;
  const visiblePages = getVisiblePages(effectivePages, clampedCurrent, readingMode, pageDirection);
  const retryingSegment = retrySegment.isPending
    ? `${retrySegment.variables?.pageIndex}:${retrySegment.variables?.segmentIndex}`
    : null;

  return (
    <main
      className="relative min-h-dvh bg-zinc-950 text-zinc-100 selection:bg-white selection:text-black"
      onClick={() => chrome.toggle()}
      onPointerMove={(event) => {
        if (event.pointerType === "mouse") chrome.keepVisible();
      }}
      onTouchMove={() => chrome.hide()}
    >
      <ReaderTopBar
        visible={chrome.visible}
        comicId={comicId}
        comicTitle={comic.data?.title ?? "ComicLens"}
        chapterTitle={manifest.data.title}
        translationEnabled={translationEnabled ?? false}
        translationBusy={startTranslation.isPending || pauseTranslation.isPending}
        retranslating={retranslate.isPending}
        task={task.data}
        onToggleTranslation={toggleTranslation}
        onRetranslate={retranslateChapter}
      />

      {actionError && (
        <div
          className="fixed top-24 left-1/2 z-40 flex w-[min(42rem,calc(100vw-1rem))] -translate-x-1/2 items-start gap-3 rounded-full border border-amber-300/25 bg-zinc-950/90 px-4 py-3 text-xs text-amber-100 shadow-xl backdrop-blur-xl"
          onClick={(event) => event.stopPropagation()}
        >
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
          <p className="min-w-0 flex-1 truncate">{actionError}。请检查服务器翻译设置。</p>
          <a href="/settings" className="shrink-0 underline underline-offset-4">
            设置
          </a>
          <button
            type="button"
            onClick={() => setActionError(null)}
            className="flex size-5 shrink-0 items-center justify-center rounded-full hover:bg-white/10"
            aria-label="关闭提示"
          >
            <XIcon className="size-3.5" />
          </button>
        </div>
      )}

      {readingMode === "strip" ? (
        <section className="mx-auto flex max-w-[72rem] flex-col items-center pb-28">
          {effectivePages.map((page) => (
            <ReaderPageImage
              key={page.index}
              page={page}
              translationEnabled={translationEnabled ?? false}
              retryingSegment={retryingSegment}
              onRetrySegment={(pageIndex, segmentIndex) =>
                retrySegment.mutate({ pageIndex, segmentIndex })
              }
              elementRef={(element) => {
                if (element) pageElements.current.set(page.index, element);
                else pageElements.current.delete(page.index);
              }}
            />
          ))}
        </section>
      ) : (
        <section className="flex h-dvh items-center justify-center overflow-hidden p-2 sm:p-4">
          <div
            className={cn(
              "mx-auto flex h-full w-full max-w-[96rem] items-center justify-center gap-1 sm:gap-3",
              readingMode === "page" && "max-w-[72rem]",
            )}
          >
            {visiblePages.map((page) => (
              <ReaderPageImage
                key={page.index}
                page={page}
                paged
                translationEnabled={translationEnabled ?? false}
                retryingSegment={retryingSegment}
                onRetrySegment={(pageIndex, segmentIndex) =>
                  retrySegment.mutate({ pageIndex, segmentIndex })
                }
              />
            ))}
          </div>
        </section>
      )}

      <ReaderBottomBar
        visible={chrome.visible}
        comicId={comicId}
        currentPageIndex={clampedCurrent}
        pageCount={totalPages}
        previousChapter={previousChapter}
        nextChapter={nextChapter}
        onPageChange={jumpToPage}
        onOpenDirectory={() => setDirectoryOpen(true)}
        onOpenSettings={() => setReaderSettingsOpen(true)}
      />

      <ReaderChapterDirectory
        open={directoryOpen}
        onOpenChange={setDirectoryOpen}
        comicId={comicId}
        comicTitle={comic.data?.title ?? "ComicLens"}
        chapterId={chapterId}
        chapters={comic.data?.chapters ?? []}
      />
      <ReaderSettingsPanel
        open={readerSettingsOpen}
        onOpenChange={setReaderSettingsOpen}
        mode={readingMode}
        direction={pageDirection}
        onModeChange={changeMode}
        onDirectionChange={changeDirection}
      />
    </main>
  );
}

function mergePage(
  page: ReaderPage,
  taskPage: TranslationPageState | undefined,
): EffectiveReaderPage {
  return {
    ...page,
    width: taskPage?.width ?? page.width,
    height: taskPage?.height ?? page.height,
    effectiveStatus: taskPage?.status ?? page.translationStatus,
    effectiveError: taskPage?.error ?? page.error,
    effectiveLayers: taskPage?.translationLayers?.length
      ? taskPage.translationLayers
      : (page.translationLayers ?? []),
    segments: taskPage?.segments ?? [],
  };
}

function getVisiblePages(
  pages: EffectiveReaderPage[],
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
