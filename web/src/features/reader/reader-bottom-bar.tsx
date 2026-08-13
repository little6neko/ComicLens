import {
  BookOpenTextIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ListIcon,
  Settings2Icon,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useEffect, useState, type ReactNode } from "react";

import { Slider } from "@/components/ui/slider";
import type { ComicChapter } from "@/domain/api";
import { cn } from "@/lib/utils";
import type { ReadingMode } from "./types";

export function ReaderBottomBar({
  visible,
  comicId,
  currentPageIndex,
  pageCount,
  previousChapter,
  nextChapter,
  settingsOpen,
  readingMode,
  pageDirection,
  onPageChange,
  onOpenDirectory,
  onSettingsOpenChange,
  onModeChange,
  onDirectionChange,
}: {
  visible: boolean;
  comicId: string;
  currentPageIndex: number;
  pageCount: number;
  previousChapter: ComicChapter | undefined;
  nextChapter: ComicChapter | undefined;
  settingsOpen: boolean;
  readingMode: ReadingMode;
  pageDirection: "ltr" | "rtl";
  onPageChange: (index: number) => void;
  onOpenDirectory: () => void;
  onSettingsOpenChange: (open: boolean) => void;
  onModeChange: (mode: ReadingMode) => void;
  onDirectionChange: (direction: "ltr" | "rtl") => void;
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
        <ReaderSettingsMenu
          open={settingsOpen}
          onOpenChange={onSettingsOpenChange}
          mode={readingMode}
          direction={pageDirection}
          onModeChange={onModeChange}
          onDirectionChange={onDirectionChange}
        />
      </div>
    </footer>
  );
}

function ReaderSettingsMenu({
  open,
  onOpenChange,
  mode,
  direction,
  onModeChange,
  onDirectionChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: ReadingMode;
  direction: "ltr" | "rtl";
  onModeChange: (mode: ReadingMode) => void;
  onDirectionChange: (direction: "ltr" | "rtl") => void;
}) {
  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className="group flex min-w-14 flex-col items-center gap-1 text-[10px] text-zinc-400 transition-colors hover:text-white data-[state=open]:text-white"
        >
          <span className="flex size-10 items-center justify-center rounded-full text-zinc-200 transition-colors group-hover:bg-white/10 group-data-[state=open]:bg-white/10 [&>svg]:size-5">
            <Settings2Icon />
          </span>
          <span>阅读设置</span>
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="end"
          sideOffset={12}
          collisionPadding={8}
          className="reader-settings-menu z-[80] w-64 overflow-hidden rounded-[1.5rem] border border-white/10 bg-zinc-900/95 p-1.5 text-zinc-100 shadow-2xl shadow-black/50 outline-none backdrop-blur-2xl"
        >
          <DropdownMenu.Label className="px-3 pt-2 pb-1.5 text-xs font-medium text-zinc-400">
            阅读模式
          </DropdownMenu.Label>
          <DropdownMenu.RadioGroup
            value={mode}
            onValueChange={(value) => onModeChange(value as ReadingMode)}
          >
            <MenuRadioItem value="strip">条漫</MenuRadioItem>
            <MenuRadioItem value="page">单页</MenuRadioItem>
            <MenuRadioItem value="double">双页</MenuRadioItem>
          </DropdownMenu.RadioGroup>

          {mode !== "strip" && (
            <>
              <DropdownMenu.Separator className="my-1.5 h-px bg-white/10" />
              <DropdownMenu.Label className="px-3 pt-1 pb-1.5 text-xs font-medium text-zinc-400">
                翻页方向
              </DropdownMenu.Label>
              <DropdownMenu.RadioGroup
                value={direction}
                onValueChange={(value) => onDirectionChange(value as "ltr" | "rtl")}
                className="grid grid-cols-2 gap-1"
              >
                <DirectionRadioItem value="ltr">从左到右</DirectionRadioItem>
                <DirectionRadioItem value="rtl">从右到左</DirectionRadioItem>
              </DropdownMenu.RadioGroup>
            </>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function MenuRadioItem({ value, children }: { value: ReadingMode; children: ReactNode }) {
  return (
    <DropdownMenu.RadioItem
      value={value}
      onSelect={(event) => event.preventDefault()}
      className="relative flex h-10 cursor-default select-none items-center rounded-xl px-3 pr-9 text-sm text-zinc-200 outline-none transition-colors focus:bg-white/10 focus:text-white data-[state=checked]:text-white"
    >
      {children}
      <DropdownMenu.ItemIndicator className="absolute right-3 flex items-center justify-center">
        <CheckIcon className="size-4" />
      </DropdownMenu.ItemIndicator>
    </DropdownMenu.RadioItem>
  );
}

function DirectionRadioItem({ value, children }: { value: "ltr" | "rtl"; children: ReactNode }) {
  return (
    <DropdownMenu.RadioItem
      value={value}
      onSelect={(event) => event.preventDefault()}
      className="flex h-9 cursor-default select-none items-center justify-center rounded-full px-2 text-xs text-zinc-400 outline-none transition-colors focus:bg-white/10 focus:text-white data-[state=checked]:bg-white data-[state=checked]:text-zinc-950"
    >
      {children}
    </DropdownMenu.RadioItem>
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
