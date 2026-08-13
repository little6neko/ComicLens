export const queryKeys = {
  auth: ["auth"] as const,
  home: ["home"] as const,
  search: (query: string, page: number) => ["search", query, page] as const,
  categories: ["categories"] as const,
  category: (categoryId: string, page: number, order: string) =>
    ["category", categoryId, page, order] as const,
  creator: (kind: string, creatorId: string, page: number) =>
    ["creator", kind, creatorId, page] as const,
  ranking: (page: number) => ["ranking", page] as const,
  comic: (comicId: string) => ["comic", comicId] as const,
  manifest: (comicId: string, chapterId: string) => ["manifest", comicId, chapterId] as const,
  favorites: ["favorites"] as const,
  history: ["history"] as const,
  readChapters: (comicId: string) => ["readChapters", comicId] as const,
  settings: ["settings"] as const,
  cache: ["cache"] as const,
  backgroundTranslations: ["backgroundTranslations"] as const,
  translation: (comicId: string, chapterId: string) => ["translation", comicId, chapterId] as const,
};

export const queryTimes = {
  catalog: { staleTime: 30 * 60_000, gcTime: 6 * 60 * 60_000 },
  detail: { staleTime: 10 * 60_000, gcTime: 60 * 60_000 },
  categories: { staleTime: 12 * 60 * 60_000, gcTime: 24 * 60 * 60_000 },
  manifest: { staleTime: 60 * 60_000, gcTime: 2 * 60 * 60_000 },
};
