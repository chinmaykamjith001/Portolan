// Placeholder compass rose (8 points, single ink color). TODO(owner): replace with your drawing.
export function Logo({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none">
      <g stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round">
        <path d="M12 1.5 L13.6 10.4 L12 12 L10.4 10.4 Z" fill="currentColor" />
        <path d="M12 22.5 L10.4 13.6 L12 12 L13.6 13.6 Z" />
        <path d="M1.5 12 L10.4 10.4 L12 12 L10.4 13.6 Z" />
        <path d="M22.5 12 L13.6 13.6 L12 12 L13.6 10.4 Z" />
        <path d="M4.6 4.6 L11 10 L12 12 L10 11 Z" />
        <path d="M19.4 4.6 L14 11 L12 12 L13 10 Z" />
        <path d="M4.6 19.4 L10 13 L12 12 L11 14 Z" />
        <path d="M19.4 19.4 L13 14 L12 12 L14 13 Z" />
      </g>
    </svg>
  );
}
