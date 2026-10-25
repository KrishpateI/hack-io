import type { SVGProps } from 'react';

export type TabIconName = 'map' | 'route' | 'community' | 'safety' | 'more';

type IconProps = Omit<SVGProps<SVGSVGElement>, 'children'> & { size?: number };

function Svg({ size = 22, children, ...rest }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export function TabIcon({ name, ...props }: IconProps & { name: TabIconName }) {
  switch (name) {
    case 'map':
      return (
        <Svg {...props}>
          <path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5z" />
          <path d="M9 4v13.5M15 6.5V20" />
        </Svg>
      );
    case 'route':
      return (
        <Svg {...props}>
          <circle cx="6" cy="18" r="2.2" />
          <circle cx="18" cy="6" r="2.2" />
          <path d="M8.2 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.8" />
        </Svg>
      );
    case 'community':
      return (
        <Svg {...props}>
          <circle cx="9" cy="8" r="3.2" />
          <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
          <path d="M16 5.2a3.2 3.2 0 0 1 0 5.6M18 14.4c1.8.8 3 2.6 3 5.6" />
        </Svg>
      );
    case 'safety':
      return (
        <Svg {...props}>
          <path d="M12 3 5 6v5.5c0 4.4 2.9 8 7 9.5 4.1-1.5 7-5.1 7-9.5V6z" />
          <path d="m9 12 2.2 2.2L15.5 10" />
        </Svg>
      );
    case 'more':
    default:
      return (
        <Svg {...props}>
          <circle cx="5" cy="12" r="1.4" fill="currentColor" />
          <circle cx="12" cy="12" r="1.4" fill="currentColor" />
          <circle cx="19" cy="12" r="1.4" fill="currentColor" />
        </Svg>
      );
  }
}

/** Disclosure chevron. `direction` defaults to right (trailing); `left` suits back buttons. */
export function Chevron({
  direction = 'right',
  size = 14,
  ...props
}: IconProps & { direction?: 'right' | 'left' | 'up' | 'down' }) {
  const rotate = { right: 0, down: 90, left: 180, up: 270 }[direction];
  return (
    <Svg size={size} strokeWidth={2.4} style={{ transform: `rotate(${rotate}deg)` }} {...props}>
      <path d="m9 5 7 7-7 7" />
    </Svg>
  );
}

export function CheckGlyph({ size = 14, ...props }: IconProps) {
  return (
    <Svg size={size} strokeWidth={2.6} {...props}>
      <path d="m5 12.5 4.5 4.5L19 7.5" />
    </Svg>
  );
}
