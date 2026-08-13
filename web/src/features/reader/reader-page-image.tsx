import { cn } from "@/lib/utils";
import { ReaderTranslationCanvas } from "./reader-translation-canvas";
import type { EffectiveReaderPage } from "./types";

export function ReaderPageImage({
  page,
  translationEnabled,
  totalSegments,
  retryingSegment,
  onRetrySegment,
  paged = false,
  elementRef,
}: {
  page: EffectiveReaderPage;
  translationEnabled: boolean;
  totalSegments: number;
  retryingSegment: string | null;
  onRetrySegment: (pageIndex: number, segmentIndex: number) => void;
  paged?: boolean;
  elementRef?: (element: HTMLElement | null) => void;
}) {
  return (
    <article
      ref={elementRef}
      data-page={page.index}
      className={cn(
        "relative w-full",
        paged ? "flex min-h-0 min-w-0 flex-1 items-center justify-center" : "max-w-[72rem]",
      )}
    >
      {translationEnabled ? (
        <ReaderTranslationCanvas
          page={page}
          paged={paged}
          totalSegments={totalSegments}
          retryingSegment={retryingSegment}
          onRetrySegment={onRetrySegment}
        />
      ) : (
        <img
          src={page.originalUrl}
          alt={`第 ${page.index + 1} 页原图`}
          loading={page.index < 2 ? "eager" : "lazy"}
          draggable={false}
          className={cn(
            "block max-w-full object-contain",
            paged ? "max-h-[100dvh] h-auto w-auto" : "h-auto w-full",
          )}
        />
      )}
    </article>
  );
}
