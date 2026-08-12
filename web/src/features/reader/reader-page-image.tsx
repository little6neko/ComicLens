import { LoaderCircleIcon, RotateCwIcon, TriangleAlertIcon } from "lucide-react";
import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";
import type { EffectiveReaderPage } from "./types";

export function ReaderPageImage({
  page,
  translationEnabled,
  retryingSegment,
  onRetrySegment,
  paged = false,
  elementRef,
}: {
  page: EffectiveReaderPage;
  translationEnabled: boolean;
  retryingSegment: string | null;
  onRetrySegment: (pageIndex: number, segmentIndex: number) => void;
  paged?: boolean;
  elementRef?: (element: HTMLElement | null) => void;
}) {
  const failedSegments = translationEnabled
    ? page.segments.filter((segment) => segment.status === "failed" && segment.error)
    : [];

  return (
    <article
      ref={elementRef}
      data-page={page.index}
      className={cn(
        "relative w-full",
        paged ? "flex min-h-0 min-w-0 flex-1 items-center justify-center" : "max-w-[72rem]",
      )}
    >
      <div
        className={cn(
          "relative max-w-full overflow-hidden bg-zinc-900",
          paged ? "inline-block max-h-[100dvh] w-fit" : "w-full",
        )}
      >
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

        {translationEnabled &&
          page.effectiveLayers.map((layer, layerIndex) => (
            <img
              key={`${layer.generationId}:${layer.kind}:${layer.segmentIndex ?? "page"}:${layer.version}`}
              src={layer.url}
              alt=""
              aria-hidden="true"
              draggable={false}
              loading="eager"
              className="pointer-events-none absolute left-0 w-full object-fill"
              style={{
                top: `${(layer.top / layer.sourceHeight) * 100}%`,
                height: `${((layer.bottom - layer.top) / layer.sourceHeight) * 100}%`,
                zIndex: 10 + layerIndex,
              }}
            />
          ))}

        {failedSegments.map((segment) => {
          const key = `${page.index}:${segment.segmentIndex}`;
          const position = {
            top: `${(segment.displayTop / segment.sourceHeight) * 100}%`,
            height: `${((segment.displayBottom - segment.displayTop) / segment.sourceHeight) * 100}%`,
            zIndex: 100,
          } satisfies CSSProperties;
          return (
            <div
              key={key}
              style={position}
              className="pointer-events-none absolute inset-x-0 flex min-h-16 items-center justify-center px-3"
            >
              <button
                type="button"
                className="pointer-events-auto flex max-w-[min(92%,28rem)] items-center gap-2 rounded-full border border-amber-300/30 bg-zinc-950/85 px-4 py-2 text-xs text-amber-100 shadow-xl backdrop-blur-xl transition-colors hover:bg-zinc-900"
                disabled={retryingSegment === key}
                title={`${segment.error?.message ?? "翻译失败"} · ${segment.error?.code ?? ""}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onRetrySegment(page.index, segment.segmentIndex);
                }}
              >
                {retryingSegment === key ? (
                  <LoaderCircleIcon className="size-4 shrink-0 animate-spin" />
                ) : (
                  <TriangleAlertIcon className="size-4 shrink-0" />
                )}
                <span className="truncate">{segment.error?.message ?? "此分片翻译失败"}</span>
                <RotateCwIcon className="size-3.5 shrink-0" />
                <span className="shrink-0 font-medium">重新翻译</span>
              </button>
            </div>
          );
        })}
      </div>
    </article>
  );
}
