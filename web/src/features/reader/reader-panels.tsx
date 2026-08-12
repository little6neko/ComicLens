import { Dialog } from "radix-ui";
import {
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Columns2Icon,
  ImagesIcon,
  PanelTopIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, type ReactNode } from "react";

import type { ComicChapter } from "@/domain/api";
import { cn } from "@/lib/utils";
import type { ReadingMode } from "./types";

const overlayClass =
  "fixed inset-0 z-[70] bg-black/55 backdrop-blur-sm data-[state=closed]:animate-none";

export function ReaderChapterDirectory({
  open,
  onOpenChange,
  comicId,
  comicTitle,
  chapterId,
  chapters,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  comicId: string;
  comicTitle: string;
  chapterId: string;
  chapters: ComicChapter[];
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const readingOrder = useMemo(() => [...chapters].reverse(), [chapters]);

  useEffect(() => {
    if (!open) return;
    const timeout = window.setTimeout(() => {
      listRef.current?.querySelector<HTMLElement>('[data-current-chapter="true"]')?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 120);
    return () => window.clearTimeout(timeout);
  }, [chapterId, open, readingOrder.length]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={overlayClass} onClick={(event) => event.stopPropagation()} />
        <Dialog.Content
          className="fixed inset-y-0 right-0 z-[80] flex w-[min(28rem,92vw)] flex-col border-l border-white/10 bg-zinc-950 text-zinc-100 shadow-2xl outline-none"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-start gap-3 border-b border-white/10 px-5 py-5">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-lg font-semibold">章节目录</Dialog.Title>
              <Dialog.Description className="mt-1 truncate text-sm text-zinc-400">
                {comicTitle || "当前 Comic"}
              </Dialog.Description>
            </div>
            <Dialog.Close className="flex size-10 items-center justify-center rounded-full text-zinc-300 hover:bg-white/10 hover:text-white">
              <XIcon className="size-5" />
              <span className="sr-only">关闭目录</span>
            </Dialog.Close>
          </div>

          <nav ref={listRef} className="min-h-0 flex-1 overflow-y-auto p-4">
            <div className="space-y-2">
              {readingOrder.map((chapter, index) => {
                const current = chapter.chapterId === chapterId;
                return (
                  <a
                    key={chapter.chapterId}
                    href={`/reader/${encodeURIComponent(comicId)}/${encodeURIComponent(chapter.chapterId)}`}
                    data-current-chapter={current ? "true" : undefined}
                    aria-current={current ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-3 rounded-2xl border border-white/8 bg-white/[0.035] px-4 py-3 text-sm transition-colors hover:bg-white/10",
                      current && "border-white/25 bg-white/12 text-white",
                    )}
                  >
                    <span className="w-8 shrink-0 text-xs tabular-nums text-zinc-500">
                      {index + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{chapter.title}</span>
                    {current && <CheckIcon className="size-4 shrink-0" />}
                  </a>
                );
              })}
            </div>
          </nav>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ReaderSettingsPanel({
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
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={overlayClass} onClick={(event) => event.stopPropagation()} />
        <Dialog.Content
          className="fixed bottom-3 left-1/2 z-[80] w-[min(31rem,calc(100vw-1rem))] -translate-x-1/2 rounded-[1.8rem] border border-white/10 bg-zinc-950 p-5 text-zinc-100 shadow-2xl outline-none sm:bottom-5"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-lg font-semibold">阅读设置</Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-zinc-400">
                修改后保存到 ComicLens 服务器
              </Dialog.Description>
            </div>
            <Dialog.Close className="flex size-10 items-center justify-center rounded-full text-zinc-300 hover:bg-white/10 hover:text-white">
              <XIcon className="size-5" />
              <span className="sr-only">关闭阅读设置</span>
            </Dialog.Close>
          </div>

          <div className="mt-5">
            <p className="mb-2 text-xs font-medium text-zinc-400">阅读模式</p>
            <div className="grid grid-cols-3 gap-2 rounded-[1.4rem] bg-white/5 p-1.5">
              <Choice
                active={mode === "strip"}
                label="纵向"
                icon={<ImagesIcon />}
                onClick={() => onModeChange("strip")}
              />
              <Choice
                active={mode === "page"}
                label="单页"
                icon={<PanelTopIcon />}
                onClick={() => onModeChange("page")}
              />
              <Choice
                active={mode === "double"}
                label="双页"
                icon={<Columns2Icon />}
                onClick={() => onModeChange("double")}
              />
            </div>
          </div>

          <div className="mt-4">
            <p className="mb-2 text-xs font-medium text-zinc-400">翻页方向</p>
            <div className="grid grid-cols-2 gap-2 rounded-[1.4rem] bg-white/5 p-1.5">
              <Choice
                active={direction === "ltr"}
                label="从左到右"
                icon={<ChevronRightIcon />}
                onClick={() => onDirectionChange("ltr")}
              />
              <Choice
                active={direction === "rtl"}
                label="从右到左"
                icon={<ChevronLeftIcon />}
                onClick={() => onDirectionChange("rtl")}
              />
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Choice({
  active,
  label,
  icon,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-12 items-center justify-center gap-2 rounded-full text-xs transition-colors [&>svg]:size-4",
        active ? "bg-white text-zinc-950" : "text-zinc-300 hover:bg-white/8 hover:text-white",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
