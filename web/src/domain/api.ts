export interface ApiErrorPayload {
  code: string;
  message: string;
  retryable: boolean;
}

export interface ChapterSummary {
  chapterId: string;
  title: string;
  updatedLabel: string | null;
}

export interface ComicSummary {
  comicId: string;
  title: string;
  coverUrl: string;
  rating: number | null;
  isAdult: boolean;
  latestChapters: ChapterSummary[];
}

export interface ComicListPage {
  items: ComicSummary[];
  page: number;
  availablePages: number[];
  hasPrevious: boolean;
  hasNext: boolean;
}

export interface FeaturedComic {
  comicId: string;
  title: string;
  coverUrl: string;
  chapterLabel: string | null;
}

export interface HomeFeed {
  featured: FeaturedComic[];
  latest: ComicListPage;
}

export interface ComicCategory {
  categoryId: string;
  label: string;
  kind: "genre" | "source_special";
  supportedOrders: ComicOrder[];
}

export type ComicOrder = "latest" | "rating" | "views";

export interface ComicChapter {
  chapterId: string;
  title: string;
  updatedLabel: string | null;
}

export interface ComicDetail {
  comicId: string;
  title: string;
  coverUrl: string;
  rating: number | null;
  alternativeTitles: string[];
  authors: string[];
  artists: string[];
  genres: string[];
  comicType: string | null;
  status: string | null;
  summary: string;
  chapters: ComicChapter[];
}

export interface RankingPage {
  period: "week";
  result: ComicListPage;
}

export interface ReaderPage {
  index: number;
  originalUrl: string;
  translatedUrl: string | null;
  translatedPartUrls: string[];
  translatedVersion: string | null;
  width: number | null;
  height: number | null;
  translationStatus: TranslationPageStatus;
  error: TranslationError | null;
}

export interface ChapterManifest {
  comicId: string;
  chapterId: string;
  title: string;
  pages: ReaderPage[];
}

export interface FavoriteItem {
  comic: ComicSummary;
  favoritedAt: number;
}

export interface HistoryItem {
  comic: ComicSummary;
  chapterId: string;
  chapterTitle: string;
  pageIndex: number;
  totalPages: number;
  updatedAt: number;
}

export interface ReadChapterState {
  comicId: string;
  chapterIds: string[];
}

export interface SensitiveSettingState {
  configured: boolean;
  masked: string | null;
}

export interface ServerSettings {
  theme: "system" | "light" | "dark";
  readingMode: "strip" | "page" | "double";
  pageDirection: "ltr" | "rtl";
  realtimeTranslationDefault: boolean;
  sourceLanguage: string;
  targetLanguage: "ZH";
  ocrMode: "auto" | "direct" | "job";
  ocrAuthMode: "none" | "bearer" | "basic";
  ocrApiUrl: SensitiveSettingState;
  ocrToken: SensitiveSettingState;
  ocrBasicUsername: string;
  ocrBasicPassword: SensitiveSettingState;
  ocrModel: string;
  ocrPollIntervalSeconds: number;
  ocrTimeoutSeconds: number;
  ocrConcurrency: number;
  deeplxUrl: SensitiveSettingState;
  deeplxTimeoutSeconds: number;
  translationConcurrency: number;
  fallbackProxyUrl: SensitiveSettingState;
  longImageThreshold: number;
  ocrSliceHeight: number;
  ocrSliceOverlap: number;
  readingSliceHeight: number;
  cacheMaxMb: number;
  accessPasswordEnabled: boolean;
  publicListenerWarning: boolean;
}

export interface CacheStats {
  usedBytes: number;
  maxBytes: number;
  bundleCount: number;
  entryCount: number;
  overLimit: boolean;
}

export type TranslationTaskStatus =
  | "idle"
  | "queued"
  | "running"
  | "stopping_after_page"
  | "paused"
  | "completed"
  | "completed_with_errors"
  | "failed";

export type TranslationPageStatus =
  | "idle"
  | "pending"
  | "downloading"
  | "ocr"
  | "translating"
  | "rendering"
  | "completed"
  | "failed";

export interface TranslationError {
  stage: string;
  code: string;
  message: string;
  retryable: boolean;
}

export interface TranslationPageState {
  pageIndex: number;
  status: TranslationPageStatus;
  translatedUrl: string | null;
  translatedPartUrls: string[];
  translatedVersion: string | null;
  width: number | null;
  height: number | null;
  attempts: number;
  error: TranslationError | null;
}

export interface TranslationTaskState {
  comicId: string;
  chapterId: string;
  generationId: string | null;
  kind: "normal" | "retranslate" | "retry";
  status: TranslationTaskStatus;
  stopRequested: boolean;
  currentPageIndex: number | null;
  totalPages: number;
  completedPages: number;
  failedPages: number;
  pages: TranslationPageState[];
}

export interface TranslationActionResult {
  task: TranslationTaskState;
}

export interface AuthConfig {
  enabled: boolean;
}

export interface AuthSession {
  enabled: boolean;
  authenticated: boolean;
}

export type SensitiveAction =
  | { action: "keep" }
  | { action: "clear" }
  | { action: "replace"; value: string };

export type SettingsPatch = Partial<
  Omit<
    ServerSettings,
    | "targetLanguage"
    | "accessPasswordEnabled"
    | "publicListenerWarning"
    | "ocrApiUrl"
    | "ocrToken"
    | "ocrBasicPassword"
    | "deeplxUrl"
    | "fallbackProxyUrl"
  >
> & {
  ocrApiUrl?: SensitiveAction;
  ocrToken?: SensitiveAction;
  ocrBasicPassword?: SensitiveAction;
  deeplxUrl?: SensitiveAction;
  fallbackProxyUrl?: SensitiveAction;
};
