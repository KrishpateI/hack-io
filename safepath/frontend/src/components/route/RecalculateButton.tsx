/** Manual recalculation trigger (Req 16). Disabled while a calculation is in flight. */
export function RecalculateButton({ onClick, busy = false }: { onClick: () => void; busy?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-busy={busy}
      className="min-h-[var(--target-min)] rounded-[var(--radius-control)] border border-route px-4 py-1.5 text-[length:var(--text-body)] font-semibold text-route transition-colors duration-[var(--duration-fast)] active:opacity-70 focus:outline-2 focus:outline-route disabled:opacity-60"
    >
      {busy ? 'Recalculating…' : 'Recalculate route'}
    </button>
  );
}
