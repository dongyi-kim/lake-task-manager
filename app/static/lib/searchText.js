// Local title/card matching: ignore letter case and all Unicode whitespace.
export const normalizeSearchText = value => String(value ?? "").normalize("NFKC").toLowerCase().replace(/\s+/gu, "");
