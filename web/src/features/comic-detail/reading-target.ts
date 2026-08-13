import type { ComicChapter, ComicDetail, HistoryItem } from "@/domain/api";

export interface ComicReadingTarget {
  chapterId: string;
  page: number;
  label: string;
}

export function resolveComicReadingTarget(
  comic: ComicDetail,
  history: HistoryItem | undefined,
): ComicReadingTarget | null {
  if (history) {
    const chapterIndex = comic.chapters.findIndex(
      (chapter) => chapter.chapterId === history.chapterId,
    );
    if (chapterIndex >= 0) {
      return {
        chapterId: history.chapterId,
        page: history.pageIndex + 1,
        label:
          comic.chapters.length > 1
            ? `从第${chapterNumber(comic.chapters, chapterIndex)}话继续`
            : "继续阅读",
      };
    }
  }

  const firstChapter = comic.chapters[0];
  return firstChapter ? { chapterId: firstChapter.chapterId, page: 1, label: "开始阅读" } : null;
}

function chapterNumber(chapters: ComicChapter[], chapterIndex: number) {
  const sourceNumber = chapters[chapterIndex]?.title.match(
    /\b(?:chapter|ch\.?)\s*([0-9]+(?:\.[0-9]+)?)\b/i,
  )?.[1];
  return sourceNumber ?? String(chapters.length - chapterIndex);
}
