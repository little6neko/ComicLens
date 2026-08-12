import { useCallback, useEffect, useRef, useState } from "react";

const AUTO_HIDE_MS = 3000;

export function useReaderChrome(resetKey: string, heldOpen: boolean) {
  const [visible, setVisible] = useState(true);
  const timerRef = useRef<number | null>(null);
  const heldOpenRef = useRef(heldOpen);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const scheduleHide = useCallback(() => {
    clearTimer();
    if (heldOpenRef.current) return;
    timerRef.current = window.setTimeout(() => setVisible(false), AUTO_HIDE_MS);
  }, [clearTimer]);

  const show = useCallback(() => {
    setVisible(true);
    scheduleHide();
  }, [scheduleHide]);

  const keepVisible = useCallback(() => {
    setVisible(true);
    if (!heldOpenRef.current) scheduleHide();
  }, [scheduleHide]);

  const hide = useCallback(() => {
    if (heldOpenRef.current) return;
    clearTimer();
    setVisible(false);
  }, [clearTimer]);

  const toggle = useCallback(() => {
    setVisible((current) => {
      if (current) clearTimer();
      else scheduleHide();
      return !current;
    });
  }, [clearTimer, scheduleHide]);

  useEffect(() => {
    heldOpenRef.current = heldOpen;
    if (heldOpen) {
      clearTimer();
      setVisible(true);
    } else {
      scheduleHide();
    }
  }, [clearTimer, heldOpen, scheduleHide]);

  useEffect(() => {
    setVisible(true);
    scheduleHide();
  }, [resetKey, scheduleHide]);

  useEffect(() => {
    const onScroll = () => hide();
    const onKeyDown = () => show();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("keydown", onKeyDown);
      clearTimer();
    };
  }, [clearTimer, hide, show]);

  return { visible, toggle, hide, show, keepVisible };
}
