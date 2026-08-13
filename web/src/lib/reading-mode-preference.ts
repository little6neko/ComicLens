import { useSyncExternalStore } from "react";

export type ReadingMode = "strip" | "page" | "double";

export const DEFAULT_READING_MODE: ReadingMode = "strip";
export const READING_MODE_STORAGE_KEY = "comiclens-reading-mode";

const readingModeChangeEvent = "comiclens:reading-mode-change";
let memoryReadingMode: ReadingMode = DEFAULT_READING_MODE;

export function useReadingMode(): readonly [ReadingMode, (mode: ReadingMode) => void] {
  const mode = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return [mode, setReadingMode] as const;
}

export function setReadingMode(mode: ReadingMode) {
  memoryReadingMode = mode;
  try {
    window.localStorage.setItem(READING_MODE_STORAGE_KEY, mode);
  } catch {
    // Keep the current tab usable when browser storage is unavailable.
  }
  window.dispatchEvent(new Event(readingModeChangeEvent));
}

function subscribe(onStoreChange: () => void) {
  const handleLocalChange = () => onStoreChange();
  const handleStorage = (event: StorageEvent) => {
    if (event.key !== READING_MODE_STORAGE_KEY && event.key !== null) {
      return;
    }
    memoryReadingMode = normalizeReadingMode(event.key === null ? null : event.newValue);
    onStoreChange();
  };

  window.addEventListener(readingModeChangeEvent, handleLocalChange);
  window.addEventListener("storage", handleStorage);
  return () => {
    window.removeEventListener(readingModeChangeEvent, handleLocalChange);
    window.removeEventListener("storage", handleStorage);
  };
}

function getSnapshot(): ReadingMode {
  try {
    memoryReadingMode = normalizeReadingMode(window.localStorage.getItem(READING_MODE_STORAGE_KEY));
  } catch {
    // Fall back to the last in-memory value when browser storage is unavailable.
  }
  return memoryReadingMode;
}

function getServerSnapshot(): ReadingMode {
  return DEFAULT_READING_MODE;
}

function normalizeReadingMode(value: string | null): ReadingMode {
  return value === "page" || value === "double" || value === "strip" ? value : DEFAULT_READING_MODE;
}
