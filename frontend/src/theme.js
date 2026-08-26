// Applies the UI palette selected by VITE_ACTIVE_THEME (see frontend/.env).
// Each palette is a JSON map of CSS variables; we set them on :root so they
// override the defaults declared in styles.css.

export function applyTheme() {
  const active = (import.meta.env.VITE_ACTIVE_THEME || "royal_navy_gold").toUpperCase();
  const raw = import.meta.env["VITE_THEME_" + active];
  if (!raw) return;
  let palette;
  try {
    palette = JSON.parse(raw);
  } catch {
    return; // malformed palette JSON: fall back to styles.css defaults
  }
  const root = document.documentElement;
  for (const [key, value] of Object.entries(palette)) {
    root.style.setProperty(key, value);
  }
}
