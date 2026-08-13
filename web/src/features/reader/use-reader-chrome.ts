import { useCallback, useEffect, useRef, useState } from "react";

export function useReaderChrome(resetKey: string, heldOpen: boolean) {
  const [visible, setVisible] = useState(true);
  const heldOpenRef = useRef(heldOpen);

  const show = useCallback(() => setVisible(true), []);
  const keepVisible = useCallback(() => setVisible(true), []);
  const hide = useCallback(() => {
    if (!heldOpenRef.current) setVisible(false);
  }, []);
  const toggle = useCallback(() => setVisible((current) => !current), []);

  useEffect(() => {
    heldOpenRef.current = heldOpen;
    if (heldOpen) setVisible(true);
  }, [heldOpen]);

  useEffect(() => {
    setVisible(true);
  }, [resetKey]);

  useEffect(() => {
    const onScroll = () => hide();
    const onKeyDown = () => show();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [hide, show]);

  return { visible, toggle, hide, show, keepVisible };
}
