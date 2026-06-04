// Best-effort issuer-ticker detection from document text. Earnings releases name
// the listing as "(NASDAQ: PANW)", "Nasdaq Global Select Market: AAPL", or
// "(NYSE: XYZ)"; the upload modal pre-fills the override field from this the
// moment a file is chosen. The backend resolves the entity authoritatively on
// analyze — this is only a convenience. Keeping the captured symbol uppercase
// makes the match case-sensitive enough that it doesn't trip on ordinary prose.
const TICKER_RE =
  /(?:NYSE|NASDAQ|Nasdaq|NYSE American|NYSE Arca|Cboe)(?:[ ][A-Za-z][A-Za-z ]*?)?:[ ]*([A-Z]{1,5}(?:\.[A-Z])?)\b/;

/** The uppercase symbol named as the issuer's listing in `text`, or null when
 *  nothing matches. Scans only the head — the listing line sits in the dateline
 *  boilerplate, and a full scan of a large transcript would be wasteful. */
export function detectTicker(text: string): string | null {
  const m = text.slice(0, 200_000).match(TICKER_RE);
  return m ? m[1] : null;
}
