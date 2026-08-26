import type { ChapterTranslationOverview } from "@/domain/api";

export type PretranslationSelectionMode = "all" | "specific" | "from";

export function selectedChapterIds(
  mode: PretranslationSelectionMode,
  chapters: ChapterTranslationOverview[],
  specificIds: ReadonlySet<string>,
  fromChapterId: string,
) {
  if (mode === "all") return chapters.map((chapter) => chapter.chapterId);
  if (mode === "specific") {
    return chapters
      .filter((chapter) => specificIds.has(chapter.chapterId))
      .map((chapter) => chapter.chapterId);
  }
  const boundary = chapters.find((chapter) => chapter.chapterId === fromChapterId);
  if (!boundary) return [];
  return chapters
    .filter((chapter) => chapter.position >= boundary.position)
    .map((chapter) => chapter.chapterId);
}

export function chapterIdsInRange(
  chapters: ChapterTranslationOverview[],
  firstChapterId: string,
  secondChapterId: string,
) {
  const first = chapters.find((chapter) => chapter.chapterId === firstChapterId);
  const second = chapters.find((chapter) => chapter.chapterId === secondChapterId);
  if (!first || !second) return new Set<string>();
  const minimum = Math.min(first.position, second.position);
  const maximum = Math.max(first.position, second.position);
  return new Set(
    chapters
      .filter((chapter) => chapter.position >= minimum && chapter.position <= maximum)
      .map((chapter) => chapter.chapterId),
  );
}

export function selectionSummary(
  chapters: ChapterTranslationOverview[],
  chapterIds: readonly string[],
) {
  const selected = new Set(chapterIds);
  const selectedChapters = chapters.filter((chapter) => selected.has(chapter.chapterId));
  return {
    selectedCount: selectedChapters.length,
    workCount: selectedChapters.filter((chapter) => chapter.requiresWork).length,
    skippedCount: selectedChapters.filter((chapter) => !chapter.requiresWork).length,
    retryCount: selectedChapters.filter(
      (chapter) => chapter.status === "needs_retry" || chapter.status === "failed",
    ).length,
  };
}
