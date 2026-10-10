import type { ComponentType, CSSProperties, SVGProps } from 'react';

// 三维图标：四乘四图标表，格位与裁切继承 UI 规格（index、0.08 内边距、0.84 viewBox）。
const CLINICAL_ATLAS = '/assets/master/clinical-3d-icons.png';
const UTILITY_ATLAS = '/assets/master/utility-3d-icons.png';
const EXTENDED_ATLAS = '/assets/master/extended-3d-icons.png';

interface ThreeDIconProps extends Omit<SVGProps<SVGSVGElement>, 'viewBox'> {
  size?: number;
  style?: CSSProperties;
}
export type IconComponent = ComponentType<ThreeDIconProps>;

function createIcon(atlas: string, index: number, mirrored = false) {
  const column = index % 4;
  const row = Math.floor(index / 4);
  return function ThreeDIcon({ size = 24, className = '', style, ...props }: ThreeDIconProps) {
    return (
      <svg
        {...props}
        className={`three-d-icon ${className}`.trim()}
        style={mirrored ? { ...style, transform: 'scaleX(-1)' } : style}
        width={size}
        height={size}
        viewBox={`${column + 0.08} ${row + 0.08} 0.84 0.84`}
        aria-hidden="true"
        focusable="false"
      >
        <image href={atlas} x="0" y="0" width="4" height="4" preserveAspectRatio="none" />
      </svg>
    );
  };
}

export const House = createIcon(CLINICAL_ATLAS, 0);
export const ClipboardList = createIcon(CLINICAL_ATLAS, 1);
export const UserRound = createIcon(CLINICAL_ATLAS, 2);
export const Bell = createIcon(CLINICAL_ATLAS, 3);
export const Thermometer = createIcon(CLINICAL_ATLAS, 4);
export const Camera = createIcon(CLINICAL_ATLAS, 5);
export const FileText = createIcon(CLINICAL_ATLAS, 6);
export const Pill = createIcon(CLINICAL_ATLAS, 7);
export const Activity = createIcon(CLINICAL_ATLAS, 8);
export const CalendarDays = createIcon(CLINICAL_ATLAS, 9);
export const Mic = createIcon(CLINICAL_ATLAS, 10);
export const Image = createIcon(CLINICAL_ATLAS, 11);
export const Images = createIcon(CLINICAL_ATLAS, 11);
export const ShieldCheck = createIcon(CLINICAL_ATLAS, 12);
export const Clock3 = createIcon(CLINICAL_ATLAS, 13);
export const CircleAlert = createIcon(CLINICAL_ATLAS, 14);
export const Check = createIcon(CLINICAL_ATLAS, 15);

export const ChevronRight = createIcon(UTILITY_ATLAS, 0);
export const ChevronLeft = createIcon(UTILITY_ATLAS, 1);
export const X = createIcon(UTILITY_ATLAS, 2);
export const Plus = createIcon(UTILITY_ATLAS, 3);
export const ZoomIn = createIcon(UTILITY_ATLAS, 4);
export const ZoomOut = createIcon(UTILITY_ATLAS, 5);
export const RotateCw = createIcon(UTILITY_ATLAS, 6);
export const Trash2 = createIcon(UTILITY_ATLAS, 7);
export const ArrowUp = createIcon(UTILITY_ATLAS, 8);
export const ArrowDown = createIcon(UTILITY_ATLAS, 9);
export const LockKeyhole = createIcon(UTILITY_ATLAS, 10);
export const History = createIcon(UTILITY_ATLAS, 11);
export const RotateCcw = createIcon(UTILITY_ATLAS, 6, true);
export const SlidersHorizontal = createIcon(UTILITY_ATLAS, 12);
export const Sun = createIcon(UTILITY_ATLAS, 13);
export const Moon = createIcon(UTILITY_ATLAS, 14);
export const Phone = createIcon(UTILITY_ATLAS, 15);

export const CircleHelp = createIcon(EXTENDED_ATLAS, 8);
export const ArrowUpRight = createIcon(EXTENDED_ATLAS, 1);
export const Maximize2 = createIcon(EXTENDED_ATLAS, 2);
export const FileClock = createIcon(EXTENDED_ATLAS, 3);
export const Layers = createIcon(EXTENDED_ATLAS, 4);
export const WifiOff = createIcon(EXTENDED_ATLAS, 5);
export const UsersRound = createIcon(EXTENDED_ATLAS, 6);
export const LoaderCircle = createIcon(EXTENDED_ATLAS, 7);
export const Mail = createIcon(EXTENDED_ATLAS, 9);
export const LogOut = createIcon(EXTENDED_ATLAS, 10);
export const ScanLine = createIcon(EXTENDED_ATLAS, 11);
export const Keyboard = createIcon(EXTENDED_ATLAS, 12);
export const Square = createIcon(EXTENDED_ATLAS, 13);
