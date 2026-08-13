import { Dialog } from "radix-ui";
import { CheckIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import type { ComicChapter } from "@/domain/api";
import { cn } from "@/lib/utils";

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
