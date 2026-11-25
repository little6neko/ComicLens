import { useNavigate } from "@tanstack/react-router";
import { useRef, type TouchEvent, type WheelEvent } from "react";

// Match jm-boom's bottom tolerance and extra touch travel.
const bottomTolerance = 24;
const swipeDistance = 48;

export function useStripChapterAdvance(comicId: string, nextChapterId: string | undefined) {
  const navigate = useNavigate();
  const endPullStartY = useRef<number | null>(null);
  const navigating = useRef(false);

  function advance() {
    if (!nextChapterId || navigating.current) return;
    navigating.current = true;
    void navigate({
      to: "/reader/$comicId/$chapterId",
      params: { comicId, chapterId: nextChapterId },
      search: { page: 1 },
      replace: true,
      reloadDocument: true,
    }).catch(() => {
      navigating.current = false;
    });
  }

  function resetPull() {
    endPullStartY.current = null;
  }

  return {
    onWheel(event: WheelEvent<HTMLElement>) {
      if (nextChapterId && !event.ctrlKey && event.deltaY > 0 && isAtEnd()) advance();
    },
    onTouchStart: resetPull,
    onTouchMove(event: TouchEvent<HTMLElement>) {
      if (!nextChapterId || event.touches.length !== 1 || !isAtEnd()) {
        resetPull();
        return;
      }
      const y = event.touches[0].clientY;
      endPullStartY.current ??= y;
      if (endPullStartY.current - y >= swipeDistance) advance();
    },
    onTouchEnd: resetPull,
    onTouchCancel: resetPull,
  };
}

function isAtEnd() {
  const scroller = document.scrollingElement;
  if (!scroller) return false;
  return scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop <= bottomTolerance;
}
