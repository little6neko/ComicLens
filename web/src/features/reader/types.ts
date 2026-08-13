import type {
  ReaderPage,
  TranslationError,
  TranslationLayer,
  TranslationPageStatus,
  TranslationSegmentState,
} from "@/domain/api";

export type { ReadingMode } from "@/lib/reading-mode-preference";

export interface EffectiveReaderPage extends ReaderPage {
  effectiveStatus: TranslationPageStatus;
  effectiveError: TranslationError | null;
  effectiveLayers: TranslationLayer[];
  segments: TranslationSegmentState[];
}
