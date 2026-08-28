export const EDITOR_HEIGHT_KEY = "cmtEditorH";
export const EDITOR_HEIGHT_MIN = 120;
export const EDITOR_HEIGHT_MAX = 720;

export function validEditorHeight(value) {
  const height = Number(value);
  return Number.isFinite(height) && height >= EDITOR_HEIGHT_MIN && height <= EDITOR_HEIGHT_MAX
    ? Math.round(height)
    : null;
}

export function loadEditorHeight(key, fallback) {
  try {
    const saved = validEditorHeight(parseInt(localStorage.getItem(key) || "", 10));
    return saved === null ? validEditorHeight(fallback) : saved;
  } catch (error) {
    return validEditorHeight(fallback);
  }
}

export function saveEditorHeight(key, value) {
  try {
    localStorage.setItem(key, String(value));
  } catch (error) {
    // Storage can be unavailable in private browsing; editor resizing remains usable in memory.
  }
}
