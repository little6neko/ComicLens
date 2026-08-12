import type {
  ReaderPage,
  TranslationError,
  TranslationLayer,
  TranslationPageStatus,
  TranslationSegmentState,
} from "@/domain/api";

export type ReadingMode = "strip" | "page" | "double";

export interface EffectiveReaderPage extends ReaderPage {
  effectiveStatus: TranslationPageStatus;
  effectiveError: TranslationError | null;
  effectiveLayers: TranslationLayer[];
  segments: TranslationSegmentState[];
}
