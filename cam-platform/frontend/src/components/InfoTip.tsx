interface Props {
  /** Plain-language explanation shown on hover / keyboard focus. */
  text: string;
  /** What the tip is about, for the screen-reader label (e.g. "Segment"). */
  label?: string;
}

/** A small "ⓘ" affordance next to a field label. Reveals a short explanation on
 *  hover and on keyboard focus (so it's reachable without a mouse). */
export function InfoTip({ text, label }: Props) {
  return (
    <span className="infotip">
      <button
        type="button"
        className="infotip-btn"
        aria-label={label ? `About ${label}: ${text}` : text}
      >
        i
      </button>
      <span className="infotip-bubble" role="tooltip">
        {text}
      </span>
    </span>
  );
}
