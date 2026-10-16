import { useSyncExternalStore } from 'react';
import generic from '../../../ui-delivery/assets/2x/child-neutral-front@2x.png';
import infantboyspring from '../../../ui-delivery/identity/2x/infant-boy-spring@2x.png';
import infantboysummer from '../../../ui-delivery/identity/2x/infant-boy-summer@2x.png';
import infantboyautumn from '../../../ui-delivery/identity/2x/infant-boy-autumn@2x.png';
import infantboywinter from '../../../ui-delivery/identity/2x/infant-boy-winter@2x.png';
import infantgirlspring from '../../../ui-delivery/identity/2x/infant-girl-spring@2x.png';
import infantgirlsummer from '../../../ui-delivery/identity/2x/infant-girl-summer@2x.png';
import infantgirlautumn from '../../../ui-delivery/identity/2x/infant-girl-autumn@2x.png';
import infantgirlwinter from '../../../ui-delivery/identity/2x/infant-girl-winter@2x.png';
import toddlerboyspring from '../../../ui-delivery/identity/2x/toddler-boy-spring@2x.png';
import toddlerboysummer from '../../../ui-delivery/identity/2x/toddler-boy-summer@2x.png';
import toddlerboyautumn from '../../../ui-delivery/identity/2x/toddler-boy-autumn@2x.png';
import toddlerboywinter from '../../../ui-delivery/identity/2x/toddler-boy-winter@2x.png';
import toddlergirlspring from '../../../ui-delivery/identity/2x/toddler-girl-spring@2x.png';
import toddlergirlsummer from '../../../ui-delivery/identity/2x/toddler-girl-summer@2x.png';
import toddlergirlautumn from '../../../ui-delivery/identity/2x/toddler-girl-autumn@2x.png';
import toddlergirlwinter from '../../../ui-delivery/identity/2x/toddler-girl-winter@2x.png';

export interface CompanionImage { id: string; src: string; label: string }
/** Decorative current-browser preference only. No identity or clinical facts are stored. */
export const companionImages: readonly CompanionImage[] = [
  { id: 'generic', src: generic, label: '通用儿童示意形象' },
  { id: 'infant-boy-spring', src: infantboyspring, label: '坐姿 · 短发 · 春季服饰' },
  { id: 'infant-boy-summer', src: infantboysummer, label: '坐姿 · 短发 · 夏季服饰' },
  { id: 'infant-boy-autumn', src: infantboyautumn, label: '坐姿 · 短发 · 秋季服饰' },
  { id: 'infant-boy-winter', src: infantboywinter, label: '坐姿 · 短发 · 冬季服饰' },
  { id: 'infant-girl-spring', src: infantgirlspring, label: '坐姿 · 发辫 · 春季服饰' },
  { id: 'infant-girl-summer', src: infantgirlsummer, label: '坐姿 · 发辫 · 夏季服饰' },
  { id: 'infant-girl-autumn', src: infantgirlautumn, label: '坐姿 · 发辫 · 秋季服饰' },
  { id: 'infant-girl-winter', src: infantgirlwinter, label: '坐姿 · 发辫 · 冬季服饰' },
  { id: 'toddler-boy-spring', src: toddlerboyspring, label: '站姿 · 短发 · 春季服饰' },
  { id: 'toddler-boy-summer', src: toddlerboysummer, label: '站姿 · 短发 · 夏季服饰' },
  { id: 'toddler-boy-autumn', src: toddlerboyautumn, label: '站姿 · 短发 · 秋季服饰' },
  { id: 'toddler-boy-winter', src: toddlerboywinter, label: '站姿 · 短发 · 冬季服饰' },
  { id: 'toddler-girl-spring', src: toddlergirlspring, label: '站姿 · 发辫 · 春季服饰' },
  { id: 'toddler-girl-summer', src: toddlergirlsummer, label: '站姿 · 发辫 · 夏季服饰' },
  { id: 'toddler-girl-autumn', src: toddlergirlautumn, label: '站姿 · 发辫 · 秋季服饰' },
  { id: 'toddler-girl-winter', src: toddlergirlwinter, label: '站姿 · 发辫 · 冬季服饰' },
];
export const companionPreferenceKey = 'sjia.display.companion.v1';
const listeners = new Set<() => void>();
let selection: string | undefined;
function checked(value: string | null) { return companionImages.some(image => image.id === value) ? value! : 'generic'; }
function snapshot() {
  if (selection === undefined) {
    try { selection = checked(window.localStorage.getItem(companionPreferenceKey)); }
    catch { selection = 'generic'; }
  }
  return selection;
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => {
    if (event.key !== companionPreferenceKey && event.key !== null) return;
    selection = checked(event.newValue); listeners.forEach(notify => notify());
  };
  window.addEventListener('storage', changed);
  return () => { listeners.delete(listener); window.removeEventListener('storage', changed); };
}
export function chooseCompanionImage(id: string): { persisted: boolean } {
  if (!companionImages.some(image => image.id === id)) throw new Error('请选择已有示意形象。');
  let persisted = true;
  try { window.localStorage.setItem(companionPreferenceKey, id); } catch { persisted = false; }
  selection = id; listeners.forEach(notify => notify());
  return { persisted };
}
export function useCompanionPreference() {
  const id = useSyncExternalStore(subscribe, snapshot, () => 'generic');
  return { image: companionImages.find(image => image.id === id) ?? companionImages[0], choose: chooseCompanionImage };
}
