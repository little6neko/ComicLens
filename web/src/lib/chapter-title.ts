export function formatReaderChapterTitle(
  title: string | null | undefined,
  chapterId: string,
): string {
  const normalizedTitle = title?.trim();
  if (normalizedTitle) return normalizedTitle.replace(/^chapter\b/i, "Ch.");

  const normalizedId = chapterId.trim();
  const chapterMatch = /^chapter[-_\s]+(.+)$/i.exec(normalizedId);
  return chapterMatch ? `Ch. ${chapterMatch[1]}` : normalizedId;
}
