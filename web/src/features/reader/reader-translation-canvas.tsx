import { LoaderCircleIcon, RotateCwIcon, TriangleAlertIcon } from "lucide-react";
import { useState } from "react";
import type { CSSProperties } from "react";

import type { TranslationLayer, TranslationSegmentState } from "@/domain/api";
import { cn } from "@/lib/utils";
import type { EffectiveReaderPage } from "./types";

export function ReaderTranslationCanvas({
  page,
  paged,
  totalSegments,
  retryingSegment,
  onRetrySegment,
}: {
  page: EffectiveReaderPage;
  paged: boolean;
  totalSegments: number;
  retryingSegment: string | null;
  onRetrySegment: (pageIndex: number, segmentIndex: number) => void;
}) {
  const legacyPageLayer = getLegacyPageLayer(page);
  const dimensions = getCanvasDimensions(page, legacyPageLayer);

  if (!dimensions) {
    return (
      <div
        className={cn(
          "flex h-[50dvh] items-center justify-center bg-zinc-900",
          paged ? "w-full max-w-[72rem]" : "w-full",
        )}
      >
        <CanvasStatus
          failed={page.effectiveStatus === "failed"}
          message={page.effectiveError?.message}
        />
      </div>
    );
  }

  const canvasStyle = {
    aspectRatio: `${dimensions.width} / ${dimensions.height}`,
    width: paged ? `min(100%, ${(dimensions.width / dimensions.height) * 100}dvh)` : "100%",
  } satisfies CSSProperties;

  return (
    <div
      className="relative max-w-full bg-zinc-900"
      style={canvasStyle}
      aria-label={`第 ${page.index + 1} 页译图`}
    >
      {page.segments.length > 0 ? (
        page.segments.map((segment) => {
          return (
            <SegmentState
              key={`${segment.segmentIndex}:${segment.translatedVersion ?? segment.status}`}
              segment={segment}
              pageIndex={page.index}
              totalSegments={totalSegments}
              retrying={retryingSegment === `${page.index}:${segment.segmentIndex}`}
              onRetry={() => onRetrySegment(page.index, segment.segmentIndex)}
            />
          );
        })
      ) : legacyPageLayer ? (
        <LegacyTranslatedPage
          key={legacyPageLayer.url}
          pageIndex={page.index}
          layer={legacyPageLayer}
        />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center overflow-hidden">
          <CanvasStatus
            failed={page.effectiveStatus === "failed"}
            message={page.effectiveError?.message}
          />
        </div>
      )}
    </div>
  );
}

function SegmentState({
  segment,
  pageIndex,
  totalSegments,
  retrying,
  onRetry,
}: {
  segment: TranslationSegmentState;
  pageIndex: number;
  totalSegments: number;
  retrying: boolean;
  onRetry: () => void;
}) {
  const [imageState, setImageState] = useState<"loading" | "loaded" | "failed">("loading");
  const style = {
    top: `${(segment.displayTop / segment.sourceHeight) * 100}%`,
    height: `${((segment.displayBottom - segment.displayTop) / segment.sourceHeight) * 100}%`,
  } satisfies CSSProperties;
  const completedWithImage = segment.status === "completed" && segment.translatedUrl;
  const unavailable =
    segment.status === "completed" && (!segment.translatedUrl || imageState === "failed");

  return (
    <div
      className={cn(
        "absolute inset-x-0 flex min-h-0 items-center justify-center bg-zinc-900 px-3",
        segment.status === "failed" ? "z-20 overflow-visible" : "overflow-hidden",
      )}
      style={style}
    >
      {segment.status === "failed" ? (
        <SegmentFailure
          segment={segment}
          pageIndex={pageIndex}
          totalSegments={totalSegments}
          retrying={retrying}
          onRetry={onRetry}
        />
      ) : unavailable ? (
        <CanvasStatus failed message="译图加载失败，请刷新页面后重试" />
      ) : !completedWithImage || imageState !== "loaded" ? (
        <CanvasStatus />
      ) : null}

      {completedWithImage && imageState !== "failed" && (
        <img
          src={segment.translatedUrl ?? undefined}
          alt={`第 ${pageIndex + 1} 页第 ${segment.segmentIndex + 1} 片译图`}
          draggable={false}
          loading="eager"
          className={cn(
            "absolute inset-0 h-full w-full object-fill transition-opacity duration-150",
            imageState === "loaded" ? "opacity-100" : "opacity-0",
          )}
          onLoad={() => setImageState("loaded")}
          onError={() => setImageState("failed")}
        />
      )}
    </div>
  );
}

function SegmentFailure({
  segment,
  pageIndex,
  totalSegments,
  retrying,
  onRetry,
}: {
  segment: TranslationSegmentState;
  pageIndex: number;
  totalSegments: number;
  retrying: boolean;
  onRetry: () => void;
}) {
  const pagePosition = `第 ${pageIndex + 1} 页 · 第 ${segment.segmentIndex + 1} 片`;
  const chapterPosition =
    totalSegments > 0 ? `全话第 ${segment.globalIndex + 1}/${totalSegments} 片` : null;

  return (
    <div className="pointer-events-none flex max-w-[min(92%,32rem)] flex-col items-center gap-1.5 rounded-3xl bg-zinc-900/90 px-4 py-3 text-center text-xs text-amber-100 shadow-xl">
      <TriangleAlertIcon className="size-5 shrink-0" />
      <p className="font-semibold">翻译失败</p>
      <p className="text-[11px] text-zinc-300">
        {pagePosition}
        {chapterPosition ? ` · ${chapterPosition}` : ""}
      </p>
      <p
        className="max-w-full truncate text-[11px] text-amber-200/80"
        title={`${segment.error?.message ?? "此分片翻译失败"} · ${segment.error?.code ?? ""}`}
      >
        {segment.error?.message ?? "此分片翻译失败"}
      </p>
      <button
        type="button"
        className="pointer-events-auto mt-1 flex h-9 items-center gap-2 rounded-full border border-amber-300/30 bg-zinc-950/85 px-4 font-medium shadow-xl backdrop-blur-xl transition-colors hover:bg-zinc-800 disabled:opacity-60"
        disabled={retrying}
        onClick={(event) => {
          event.stopPropagation();
          onRetry();
        }}
      >
        {retrying ? (
          <LoaderCircleIcon className="size-4 animate-spin" />
        ) : (
          <RotateCwIcon className="size-4" />
        )}
        {retrying ? "正在重试" : "重新翻译"}
      </button>
    </div>
  );
}

function LegacyTranslatedPage({
  pageIndex,
  layer,
}: {
  pageIndex: number;
  layer: TranslationLayer;
}) {
  const [imageState, setImageState] = useState<"loading" | "loaded" | "failed">("loading");
  const failed = imageState === "failed";

  return (
    <div className="absolute inset-0 flex items-center justify-center overflow-hidden">
      <CanvasStatus
        failed={failed}
        message={failed ? "译图加载失败，请刷新页面后重试" : undefined}
      />
      {!failed && (
        <img
          src={layer.url}
          alt={`第 ${pageIndex + 1} 页译图`}
          draggable={false}
          loading="eager"
          className={cn(
            "absolute inset-0 h-full w-full object-fill transition-opacity duration-150",
            imageState === "loaded" ? "opacity-100" : "opacity-0",
          )}
          onLoad={() => setImageState("loaded")}
          onError={() => setImageState("failed")}
        />
      )}
    </div>
  );
}

function CanvasStatus({ failed = false, message }: { failed?: boolean; message?: string }) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 px-4 text-center text-xs",
        failed ? "text-amber-200" : "text-zinc-500",
      )}
    >
      {failed ? (
        <TriangleAlertIcon className="size-5" />
      ) : (
        <LoaderCircleIcon className="size-5 animate-spin" />
      )}
      <span>{failed ? "翻译失败" : "翻译中"}</span>
      {message && <span className="max-w-md text-[11px] opacity-80">{message}</span>}
    </div>
  );
}

function getLegacyPageLayer(page: EffectiveReaderPage) {
  if (page.segments.length > 0) return undefined;
  return [...page.effectiveLayers]
    .reverse()
    .find(
      (layer) => layer.kind === "page" && layer.top === 0 && layer.bottom === layer.sourceHeight,
    );
}

function getCanvasDimensions(
  page: EffectiveReaderPage,
  legacyPageLayer: TranslationLayer | undefined,
) {
  const segment = page.segments[0];
  const width = segment?.sourceWidth ?? page.width ?? legacyPageLayer?.sourceWidth;
  const height = segment?.sourceHeight ?? page.height ?? legacyPageLayer?.sourceHeight;
  if (!width || !height || width <= 0 || height <= 0) return null;
  return { width, height };
}
