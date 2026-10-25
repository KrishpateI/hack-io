/**
 * Shared 911 guidance text, used by the header popover and the Safety tab Card.
 * Guidance only: SafePath does not call, dispatch, or notify 911 (Req 10.3, 10.6).
 */
export function Emergency911Guidance() {
  return (
    <div className="space-y-1 text-sm text-text-primary">
      <p>If you are in immediate danger, call 911 from your phone now.</p>
      <p className="text-text-muted">
        SafePath does not call, dispatch, or notify 911 or any emergency service. You must place the call yourself.
      </p>
    </div>
  );
}
