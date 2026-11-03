// Image errors do not distinguish a missing photo from a temporary authentication/network failure.
// Suppress repeated failures briefly; successful photos keep their normal HTTP/server caching.
const failures = new Map();
const RETRY_AFTER_MS = 60000;

export function avatarFailed(user) {
  if ((failures.get(user) || 0) > Date.now()) return true;
  failures.delete(user);
  return false;
}

export function markAvatarFailed(user) {
  for (const [id, until] of failures) if (until <= Date.now()) failures.delete(id);
  failures.set(user, Date.now() + RETRY_AFTER_MS);
}

// These listeners run before the mounted Avatar/mention recovery listeners.
if (typeof window !== "undefined") {
  for (const event of ["auth-ok", "online"]) window.addEventListener(event, () => failures.clear());
}
