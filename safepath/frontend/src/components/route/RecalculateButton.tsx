/** Manual recalculation trigger (Req 16). Disabled while a calculation is in flight. */
export function RecalculateButton({ onClick, busy = false }: { onClick: () => void; busy?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-busy={busy}
      className="min-h-[44px] rounded border border-route px-3 py-1.5 text-sm font-semibold text-route focus:outline-2 focus:outline-route disabled:opacity-60"
    >
      {busy ? 'Recalculating…' : 'Recalculate route'}
    </button>
  );
}
