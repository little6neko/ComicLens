import { useSyncExternalStore } from "react";

export const DEFAULT_REALTIME_TRANSLATION = false;
export const REALTIME_TRANSLATION_STORAGE_KEY = "comiclens-realtime-translation-default";

const realtimeTranslationChangeEvent = "comiclens:realtime-translation-default-change";
let memoryRealtimeTranslation = DEFAULT_REALTIME_TRANSLATION;

export function useRealtimeTranslationDefault(): readonly [boolean, (enabled: boolean) => void] {
  const enabled = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return [enabled, setRealtimeTranslationDefault] as const;
}

export function getRealtimeTranslationDefault(): boolean {
  return getSnapshot();
}

export function setRealtimeTranslationDefault(enabled: boolean) {
  memoryRealtimeTranslation = enabled;
  try {
    window.localStorage.setItem(REALTIME_TRANSLATION_STORAGE_KEY, String(enabled));
  } catch {
    // Keep the current tab usable when browser storage is unavailable.
  }
  window.dispatchEvent(new Event(realtimeTranslationChangeEvent));
}

function subscribe(onStoreChange: () => void) {
  const handleLocalChange = () => onStoreChange();
  const handleStorage = (event: StorageEvent) => {
    if (event.key !== REALTIME_TRANSLATION_STORAGE_KEY && event.key !== null) {
      return;
    }
    memoryRealtimeTranslation = normalizeRealtimeTranslation(
      event.key === null ? null : event.newValue,
    );
    onStoreChange();
  };

  window.addEventListener(realtimeTranslationChangeEvent, handleLocalChange);
  window.addEventListener("storage", handleStorage);
  return () => {
    window.removeEventListener(realtimeTranslationChangeEvent, handleLocalChange);
    window.removeEventListener("storage", handleStorage);
  };
}

function getSnapshot(): boolean {
  try {
    memoryRealtimeTranslation = normalizeRealtimeTranslation(
      window.localStorage.getItem(REALTIME_TRANSLATION_STORAGE_KEY),
    );
  } catch {
    // Fall back to the last in-memory value when browser storage is unavailable.
  }
  return memoryRealtimeTranslation;
}

function getServerSnapshot(): boolean {
  return DEFAULT_REALTIME_TRANSLATION;
}

function normalizeRealtimeTranslation(value: string | null): boolean {
  if (value === "true") return true;
  if (value === "false") return false;
  return DEFAULT_REALTIME_TRANSLATION;
}
