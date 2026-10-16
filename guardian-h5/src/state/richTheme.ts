import { useCallback, useEffect, useState } from 'react';

export type RichThemePreference = 'system' | 'light' | 'dark';
export type RichTheme = 'light' | 'dark';
// This key contains only a device appearance preference, never account or health data.
export const richThemeStorageKey = 'sjia.ui.theme';
const themeChangeEvent = 'sjia:appearance-change';
const darkQuery = '(prefers-color-scheme: dark)';

export function isRichThemePreference(value: unknown): value is RichThemePreference {
  return value === 'system' || value === 'light' || value === 'dark';
}

function readPreference(): RichThemePreference {
  try {
    const value = window.localStorage.getItem(richThemeStorageKey);
    return isRichThemePreference(value) ? value : 'system';
  } catch { return 'system'; } // Storage may be unavailable in private/embedded contexts.
}

function systemTheme(): RichTheme {
  return typeof window.matchMedia === 'function' && window.matchMedia(darkQuery).matches ? 'dark' : 'light';
}

export function useRichTheme() {
  const [preference, setPreference] = useState<RichThemePreference>(readPreference);
  const [system, setSystem] = useState<RichTheme>(systemTheme);
  useEffect(() => {
    const media = typeof window.matchMedia === 'function' ? window.matchMedia(darkQuery) : null;
    const updateSystem = () => setSystem(media?.matches ? 'dark' : 'light');
    updateSystem();
    if (media?.addEventListener) media.addEventListener('change', updateSystem);
    else media?.addListener?.(updateSystem);
    const updateStorage = (event: StorageEvent) => {
      try { if (event.storageArea && event.storageArea !== window.localStorage) return; } catch { return; }
      if (event.key === richThemeStorageKey || event.key === null) {
        setPreference(isRichThemePreference(event.newValue) ? event.newValue : 'system');
      }
    };
    const updateCurrentWindow = (event: Event) => {
      const value: unknown = (event as CustomEvent<unknown>).detail;
      if (isRichThemePreference(value)) setPreference(value);
    };
    window.addEventListener('storage', updateStorage);
    window.addEventListener(themeChangeEvent, updateCurrentWindow);
    return () => {
      if (media?.removeEventListener) media.removeEventListener('change', updateSystem);
      else media?.removeListener?.(updateSystem);
      window.removeEventListener('storage', updateStorage);
      window.removeEventListener(themeChangeEvent, updateCurrentWindow);
    };
  }, []);
  const changePreference = useCallback((value: unknown) => {
    if (!isRichThemePreference(value)) return;
    setPreference(value);
    try { window.localStorage.setItem(richThemeStorageKey, value); } catch { /* The current page still works. */ }
    window.dispatchEvent(new CustomEvent(themeChangeEvent, { detail: value }));
  }, []);
  return { preference, theme: preference === 'system' ? system : preference, changePreference };
}
