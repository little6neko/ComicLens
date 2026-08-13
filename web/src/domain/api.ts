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

export interface ComicMetadataItem {
  label: string;
  slug: string | null;
}

export type ComicCreatorKind = "author" | "artist";

export interface ComicCreatorArchive {
  kind: ComicCreatorKind;
  creatorId: string;
  label: string;
  result: ComicListPage;
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
  authors: ComicMetadataItem[];
  artists: ComicMetadataItem[];
  genres: ComicMetadataItem[];
  comicType: string | null;
  releaseLabel: string | null;
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
  translationLayers: TranslationLayer[];
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
  sourceLanguage: "AUTO" | "EN" | "KO";
  targetLanguage: "ZH-HANS";
  ocrApiUrl: SensitiveSettingState;
  ocrToken: SensitiveSettingState;
  ocrModel: string;
  ocrPollIntervalSeconds: number;
  ocrTimeoutSeconds: number;
  ocrConcurrency: number;
  translationService: "deepl" | "deeplx";
  deeplApiKey: SensitiveSettingState;
  deeplxUrl: SensitiveSettingState;
  translationTimeoutSeconds: number;
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
  | "preparing"
  | "queued"
  | "running"
  | "stopping_after_page"
  | "stopping_after_segment"
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
  segments: TranslationSegmentState[];
  translationLayers: TranslationLayer[];
}

export type TranslationSegmentStatus =
  | "pending"
  | "ocr"
  | "translating"
  | "rendering"
  | "completed"
  | "failed";

export interface TranslationLayer {
  kind: "page" | "segment";
  generationId: string;
  segmentIndex: number | null;
  top: number;
  bottom: number;
  sourceWidth: number;
  sourceHeight: number;
  url: string;
  version: string;
}

export interface TranslationSegmentState {
  pageIndex: number;
  segmentIndex: number;
  globalIndex: number;
  status: TranslationSegmentStatus;
  displayTop: number;
  displayBottom: number;
  sourceWidth: number;
  sourceHeight: number;
  translatedUrl: string | null;
  translatedVersion: string | null;
  attempts: number;
  error: TranslationError | null;
}

export interface CurrentTranslationSegment {
  pageIndex: number;
  segmentIndex: number;
}

export interface TranslationTaskState {
  comicId: string;
  chapterId: string;
  generationId: string | null;
  kind: "normal" | "retranslate" | "retry";
  status: TranslationTaskStatus;
  stopRequested: boolean;
  currentPageIndex: number | null;
  currentSegment: CurrentTranslationSegment | null;
  totalPages: number;
  completedPages: number;
  failedPages: number;
  planningComplete: boolean;
  totalSegments: number;
  completedSegments: number;
  failedSegments: number;
  pages: TranslationPageState[];
}

export interface TranslationActionResult {
  task: TranslationTaskState;
}

export type BackgroundTranslationStage =
  | "preparing"
  | "queued"
  | "ocr"
  | "translating"
  | "rendering"
  | "stopping"
  | "processing";

export interface BackgroundTranslationTask {
  comicId: string;
  chapterId: string;
  comicTitle: string;
  chapterTitle: string;
  generationId: string;
  kind: "normal" | "retranslate" | "retry";
  status: TranslationTaskStatus;
  stage: BackgroundTranslationStage;
  currentPageIndex: number | null;
  currentSegment: CurrentTranslationSegment | null;
  planningComplete: boolean;
  totalPages: number;
  preparedPages: number;
  completedPages: number;
  failedPages: number;
  totalSegments: number;
  completedSegments: number;
  failedSegments: number;
}

export interface ForceStopTranslationResult {
  comicId: string;
  chapterId: string;
  stoppedGenerations: number;
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
    | "deeplApiKey"
    | "deeplxUrl"
    | "fallbackProxyUrl"
  >
> & {
  ocrApiUrl?: SensitiveAction;
  ocrToken?: SensitiveAction;
  deeplApiKey?: SensitiveAction;
  deeplxUrl?: SensitiveAction;
  fallbackProxyUrl?: SensitiveAction;
};
