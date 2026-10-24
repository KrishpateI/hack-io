export type IconKind = 'check' | 'question' | 'cross' | 'flask' | 'people' | 'warning' | 'building';

/** Encoding-safe inline SVG icons (decorative; the adjacent text carries the meaning). */
export function StatusIcon({ kind }: { kind: IconKind }) {
  const common = {
    width: 12,
    height: 12,
    viewBox: '0 0 12 12',
    'aria-hidden': true as const,
    focusable: false as const,
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.6,
  };
  switch (kind) {
    case 'check':
      return (<svg {...common}><path d="M2 6.5 5 9l5-6" /></svg>);
    case 'cross':
      return (<svg {...common}><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" /></svg>);
    case 'question':
      return (<svg {...common}><circle cx="6" cy="6" r="4.5" /><path d="M4.7 4.8a1.4 1.4 0 1 1 2 1.2c-.5.3-.7.6-.7 1.1M6 8.8v.1" /></svg>);
    case 'flask':
      return (<svg {...common}><path d="M4.5 1.5h3M5 1.5v3L2.5 9.5h7L7 4.5v-3" /></svg>);
    case 'people':
      return (<svg {...common}><circle cx="4" cy="4" r="1.5" /><circle cx="8.5" cy="4.5" r="1.2" /><path d="M1.5 10c0-2 1-3 2.5-3s2.5 1 2.5 3M7 8c.4-.3.9-.5 1.5-.5 1 0 2 .7 2 2.5" /></svg>);
    case 'warning':
      return (<svg {...common}><path d="M6 1.5 11 10H1z" /><path d="M6 5v2.5M6 8.8v.1" /></svg>);
    case 'building':
      return (<svg {...common}><path d="M2.5 10.5v-8h5v8M7.5 5h2v5.5M1.5 10.5h9" /></svg>);
  }
}
