import {
  BookOpenTextIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ListIcon,
  Settings2Icon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Slider } from "@/components/ui/slider";
import type { ComicChapter } from "@/domain/api";
import { cn } from "@/lib/utils";

export function ReaderBottomBar({
  visible,
  comicId,
  currentPageIndex,
  pageCount,
  previousChapter,
  nextChapter,
  onPageChange,
  onOpenDirectory,
  onOpenSettings,
}: {
  visible: boolean;
  comicId: string;
  currentPageIndex: number;
  pageCount: number;
  previousChapter: ComicChapter | undefined;
  nextChapter: ComicChapter | undefined;
  onPageChange: (index: number) => void;
  onOpenDirectory: () => void;
  onOpenSettings: () => void;
}) {
  const [previewIndex, setPreviewIndex] = useState(currentPageIndex);

  useEffect(() => {
    setPreviewIndex(currentPageIndex);
  }, [currentPageIndex]);

  if (pageCount <= 0) return null;

  return (
    <footer
      className={cn(
        "fixed bottom-3 left-1/2 z-50 w-[min(31rem,calc(100vw-1rem))] -translate-x-1/2 rounded-[1.8rem] border border-white/10 bg-zinc-950/84 px-4 pt-3 pb-[max(.75rem,env(safe-area-inset-bottom))] text-zinc-100 shadow-2xl shadow-black/40 backdrop-blur-2xl transition-all duration-200 sm:bottom-5",
        visible ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-5 opacity-0",
      )}
      onClick={(event) => event.stopPropagation()}
      onTouchMove={(event) => event.stopPropagation()}
    >
      <div className="flex items-center gap-3">
        <Slider
          min={0}
          max={Math.max(0, pageCount - 1)}
          step={1}
          value={[previewIndex]}
          disabled={pageCount <= 1}
          aria-label="源图片阅读进度"
          aria-valuetext={`第 ${currentPageIndex + 1} 张，共 ${pageCount} 张`}
          onValueChange={(values) => setPreviewIndex(values[0] ?? currentPageIndex)}
          onValueCommit={(values) => onPageChange(values[0] ?? currentPageIndex)}
        />
        <span className="w-14 shrink-0 text-right text-xs tabular-nums text-zinc-300">
          {previewIndex + 1} / {pageCount}
        </span>
      </div>

      <div className="mt-2 flex items-start justify-around gap-1">
        <ChapterAction
          label="上一话"
          icon={<ChevronLeftIcon />}
          comicId={comicId}
          chapter={previousChapter}
        />
        <ChapterAction
          label="下一话"
          icon={<ChevronRightIcon />}
          comicId={comicId}
          chapter={nextChapter}
        />
        <RoundAction label="目录" icon={<ListIcon />} onClick={onOpenDirectory} />
        <RoundAction label="阅读设置" icon={<Settings2Icon />} onClick={onOpenSettings} />
      </div>
    </footer>
  );
}

function ChapterAction({
  label,
  icon,
  comicId,
  chapter,
}: {
  label: string;
  icon: ReactNode;
  comicId: string;
  chapter: ComicChapter | undefined;
}) {
  if (!chapter) return <RoundAction label={label} icon={icon} disabled />;
  return (
    <a
      href={`/reader/${encodeURIComponent(comicId)}/${encodeURIComponent(chapter.chapterId)}`}
      className="group flex min-w-14 flex-col items-center gap-1 text-[10px] text-zinc-400 transition-colors hover:text-white"
      title={`${label}：${chapter.title}`}
    >
      <span className="flex size-10 items-center justify-center rounded-full text-zinc-200 transition-colors group-hover:bg-white/10 [&>svg]:size-5">
        {icon}
      </span>
      <span>{label}</span>
    </a>
  );
}

function RoundAction({
  label,
  icon,
  onClick,
  disabled = false,
}: {
  label: string;
  icon: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "group flex min-w-14 flex-col items-center gap-1 text-[10px] text-zinc-400 transition-colors hover:text-white disabled:opacity-30",
      )}
    >
      <span className="flex size-10 items-center justify-center rounded-full text-zinc-200 transition-colors group-hover:bg-white/10 group-disabled:bg-transparent [&>svg]:size-5">
        {icon ?? <BookOpenTextIcon />}
      </span>
      <span>{label}</span>
    </button>
  );
}
