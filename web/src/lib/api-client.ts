import type {
  ApiErrorPayload,
  AuthConfig,
  AuthSession,
  BackgroundTranslationTask,
  CacheStats,
  ChapterManifest,
  ComicCategory,
  ComicCreatorArchive,
  ComicCreatorKind,
  ComicDetail,
  ComicListPage,
  ComicOrder,
  FavoriteItem,
  ForceStopTranslationResult,
  HistoryItem,
  HomeFeed,
  RankingPage,
  ReadChapterState,
  RetryFailedTranslationResult,
  ServerSettings,
  SettingsPatch,
  TranslationActionResult,
  TranslationTaskState,
} from "@/domain/api";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message);
    this.name = "ApiError";
    this.code = payload.code;
    this.status = status;
    this.retryable = payload.retryable;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let payload: ApiErrorPayload = {
      code: "HTTP_ERROR",
      message: `请求失败 (${response.status})`,
      retryable: response.status >= 500,
    };
    try {
      payload = (await response.json()) as ApiErrorPayload;
    } catch {
      // Keep the stable local fallback when an intermediary returns non-JSON.
    }
    if (response.status === 401 && payload.code === "AUTH_REQUIRED") {
      const next = `${window.location.pathname}${window.location.search}`;
      window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    }
    throw new ApiError(response.status, payload);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(path: string, values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const serialized = params.toString();
  return serialized ? `${path}?${serialized}` : path;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  authConfig: () => request<AuthConfig>("/api/auth/config"),
  authSession: () => request<AuthSession>("/api/auth/session"),
  login: (password: string) => request<AuthSession>("/api/auth/login", json("POST", { password })),
  logout: () => request<AuthSession>("/api/auth/logout", json("POST")),
  home: () => request<HomeFeed>("/api/home/feed"),
  latest: (page: number) => request<ComicListPage>(query("/api/home/latest", { page })),
  search: (q: string, page: number) =>
    request<ComicListPage>(query("/api/comics/search", { q, page })),
  categories: () => request<ComicCategory[]>("/api/comics/categories"),
  category: (categoryId: string, page: number, order: ComicOrder) =>
    request<ComicListPage>(
      query(`/api/comics/categories/${encodeURIComponent(categoryId)}`, { page, order }),
    ),
  creator: (kind: ComicCreatorKind, creatorId: string, page: number) =>
    request<ComicCreatorArchive>(
      query(`/api/comics/creators/${encodeURIComponent(kind)}/${encodeURIComponent(creatorId)}`, {
        page,
      }),
    ),
  ranking: (page: number) => request<RankingPage>(query("/api/comics/ranking", { page })),
  comic: (comicId: string) => request<ComicDetail>(`/api/comics/${encodeURIComponent(comicId)}`),
  manifest: (comicId: string, chapterId: string) =>
    request<ChapterManifest>(
      `/api/comics/${encodeURIComponent(comicId)}/chapters/${encodeURIComponent(chapterId)}/manifest`,
    ),
  favorites: () => request<FavoriteItem[]>("/api/favorites"),
  saveFavorite: (comicId: string, comic: ComicDetail | FavoriteItem["comic"]) =>
    request<FavoriteItem>(
      `/api/favorites/${encodeURIComponent(comicId)}`,
      json("PUT", snapshot(comic)),
    ),
  deleteFavorite: (comicId: string) =>
    request<void>(`/api/favorites/${encodeURIComponent(comicId)}`, json("DELETE")),
  clearFavorites: () => request<void>("/api/favorites", json("DELETE")),
  history: () => request<HistoryItem[]>("/api/history"),
  saveHistory: (
    comic: ComicDetail,
    chapterId: string,
    chapterTitle: string,
    pageIndex: number,
    totalPages: number,
  ) =>
    request<HistoryItem>(
      `/api/history/${encodeURIComponent(comic.comicId)}`,
      json("PUT", {
        ...snapshot(comic),
        chapterId,
        chapterTitle,
        pageIndex,
        totalPages,
      }),
    ),
  deleteHistory: (comicId: string) =>
    request<void>(`/api/history/${encodeURIComponent(comicId)}`, json("DELETE")),
  clearHistory: () => request<void>("/api/history", json("DELETE")),
  readChapters: (comicId: string) =>
    request<ReadChapterState>(`/api/comics/${encodeURIComponent(comicId)}/read-chapters`),
  setChapterRead: (comicId: string, chapterId: string, read = true) =>
    request<ReadChapterState>(
      `/api/comics/${encodeURIComponent(comicId)}/read-chapters/${encodeURIComponent(chapterId)}`,
      json("PUT", { read }),
    ),
  settings: () => request<ServerSettings>("/api/settings"),
  patchSettings: (patch: SettingsPatch) =>
    request<ServerSettings>("/api/settings", json("PATCH", patch)),
  cacheStats: () => request<CacheStats>("/api/system/cache"),
  clearCache: () => request<void>("/api/system/cache", json("DELETE")),
  clearChapterCache: (comicId: string, chapterId: string) =>
    request<void>(
      `/api/system/cache/comics/${encodeURIComponent(comicId)}/chapters/${encodeURIComponent(chapterId)}`,
      json("DELETE"),
    ),
  backgroundTranslations: () =>
    request<BackgroundTranslationTask[]>("/api/translations/background"),
  translation: (comicId: string, chapterId: string) =>
    request<TranslationTaskState>(translationPath(comicId, chapterId)),
  startTranslation: (comicId: string, chapterId: string) =>
    request<TranslationActionResult>(`${translationPath(comicId, chapterId)}/start`, json("POST")),
  pauseTranslation: (comicId: string, chapterId: string) =>
    request<TranslationActionResult>(`${translationPath(comicId, chapterId)}/pause`, json("POST")),
  forceStopTranslation: (comicId: string, chapterId: string) =>
    request<ForceStopTranslationResult>(
      `${translationPath(comicId, chapterId)}/force-stop`,
      json("POST"),
    ),
  retranslate: (comicId: string, chapterId: string) =>
    request<TranslationActionResult>(
      `${translationPath(comicId, chapterId)}/retranslate`,
      json("POST", { confirmed: true }),
    ),
  retryFailedTranslation: (comicId: string, chapterId: string) =>
    request<RetryFailedTranslationResult>(
      `${translationPath(comicId, chapterId)}/retry-failed`,
      json("POST"),
    ),
  retryPage: (comicId: string, chapterId: string, pageIndex: number) =>
    request<TranslationActionResult>(
      `${translationPath(comicId, chapterId)}/pages/${pageIndex}/retry`,
      json("POST"),
    ),
  retrySegment: (comicId: string, chapterId: string, pageIndex: number, segmentIndex: number) =>
    request<TranslationActionResult>(
      `${translationPath(comicId, chapterId)}/pages/${pageIndex}/segments/${segmentIndex}/retry`,
      json("POST"),
    ),
};

function translationPath(comicId: string, chapterId: string) {
  return `/api/comics/${encodeURIComponent(comicId)}/chapters/${encodeURIComponent(chapterId)}/translation`;
}

function snapshot(comic: ComicDetail | FavoriteItem["comic"]) {
  return {
    title: comic.title,
    rating: comic.rating,
    isAdult: "isAdult" in comic ? comic.isAdult : false,
    latestChapters: "latestChapters" in comic ? comic.latestChapters : [],
  };
}
