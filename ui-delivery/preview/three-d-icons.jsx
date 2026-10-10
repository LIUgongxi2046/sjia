import React from 'react';
import clinicalAtlas from '../assets/master/clinical-3d-icons.png';
import utilityAtlas from '../assets/master/utility-3d-icons.png';
import extendedAtlas from '../assets/master/extended-3d-icons.png';

function createIcon(atlas, index, mirrored = false) {
  const column = index % 4;
  const row = Math.floor(index / 4);
  return function ThreeDIcon({size = 24, strokeWidth, className = '', style, ...props}) {
    return <svg {...props} className={`three-d-icon ${className}`.trim()} style={mirrored ? {...style, transform:'scaleX(-1)'} : style} width={size} height={size} viewBox={`${column + 0.08} ${row + 0.08} 0.84 0.84`} aria-hidden="true" focusable="false">
      <image href={atlas} x="0" y="0" width="4" height="4" preserveAspectRatio="none"/>
    </svg>;
  };
}

export const House = createIcon(clinicalAtlas, 0);
export const ClipboardList = createIcon(clinicalAtlas, 1);
export const UserRound = createIcon(clinicalAtlas, 2);
export const Bell = createIcon(clinicalAtlas, 3);
export const Thermometer = createIcon(clinicalAtlas, 4);
export const Camera = createIcon(clinicalAtlas, 5);
export const FileText = createIcon(clinicalAtlas, 6);
export const Pill = createIcon(clinicalAtlas, 7);
export const Activity = createIcon(clinicalAtlas, 8);
export const CalendarDays = createIcon(clinicalAtlas, 9);
export const Mic = createIcon(clinicalAtlas, 10);
export const Image = createIcon(clinicalAtlas, 11);
export const Images = createIcon(clinicalAtlas, 11);
export const ShieldCheck = createIcon(clinicalAtlas, 12);
export const Clock3 = createIcon(clinicalAtlas, 13);
export const CircleAlert = createIcon(clinicalAtlas, 14);
export const Check = createIcon(clinicalAtlas, 15);

export const ChevronRight = createIcon(utilityAtlas, 0);
export const ChevronLeft = createIcon(utilityAtlas, 1);
export const X = createIcon(utilityAtlas, 2);
export const Plus = createIcon(utilityAtlas, 3);
export const ZoomIn = createIcon(utilityAtlas, 4);
export const ZoomOut = createIcon(utilityAtlas, 5);
export const RotateCw = createIcon(utilityAtlas, 6);
export const Trash2 = createIcon(utilityAtlas, 7);
export const ArrowUp = createIcon(utilityAtlas, 8);
export const ArrowDown = createIcon(utilityAtlas, 9);
export const LockKeyhole = createIcon(utilityAtlas, 10);
export const History = createIcon(utilityAtlas, 11);
export const RotateCcw = createIcon(utilityAtlas, 6, true);
export const SlidersHorizontal = createIcon(utilityAtlas, 12);
export const Sun = createIcon(utilityAtlas, 13);
export const Moon = createIcon(utilityAtlas, 14);
export const Phone = createIcon(utilityAtlas, 15);

export const CircleHelp = createIcon(extendedAtlas, 8);
export const ArrowUpRight = createIcon(extendedAtlas, 1);
export const Maximize2 = createIcon(extendedAtlas, 2);
export const FileClock = createIcon(extendedAtlas, 3);
export const Layers = createIcon(extendedAtlas, 4);
export const WifiOff = createIcon(extendedAtlas, 5);
export const UsersRound = createIcon(extendedAtlas, 6);
export const LoaderCircle = createIcon(extendedAtlas, 7);
export const Mail = createIcon(extendedAtlas, 9);
export const LogOut = createIcon(extendedAtlas, 10);
export const ScanLine = createIcon(extendedAtlas, 11);
export const Keyboard = createIcon(extendedAtlas, 12);
export const Square = createIcon(extendedAtlas, 13);
