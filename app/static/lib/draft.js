// Durable rich drafts. Resolve only after the transaction commits, not request success.
const DB_NAME = "ltm-drafts", STORE = "drafts";
export const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;
let connection;
const queues = new Map();

function idb() {
  if (!connection) connection = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE);
    };
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => { db.close(); connection = null; };
      db.onclose = () => { connection = null; };
      resolve(db);
    };
    request.onerror = () => reject(request.error);
  }).catch(error => { connection = null; throw error; });
  return connection;
}

async function op(mode, fn) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    let result;
    transaction.oncomplete = () => resolve(result);
    transaction.onabort = transaction.onerror = () => reject(transaction.error || new Error("초안 저장 실패"));
    const request = fn(transaction.objectStore(STORE));
    request.onsuccess = () => { result = request.result; };
  });
}

function ordered(key, fn) {
  const prior = queues.get(key) || Promise.resolve();
  const current = prior.catch(() => {}).then(fn);
  queues.set(key, current);
  current.finally(() => { if (queues.get(key) === current) queues.delete(key); }).catch(() => {});
  return current;
}

export function saveDraft(key, record) {
  const payload = structuredClone({ ...record, savedAt: Date.now() });
  return ordered(key, () => op("readwrite", store => store.put(payload, key)));
}
export function clearDraft(key) {
  return ordered(key, () => op("readwrite", store => store.delete(key)));
}
export function loadDraft(key) {
  return ordered(key, async () => {
    const record = await op("readonly", store => store.get(key));
    if (!record) return null;
    // Mutation evidence is not an ordinary editor draft. Keep it for manual diagnosis even
    // after replay has expired; the caller independently forbids old writes by firstAttemptAt.
    if (record.preserveExpiredForMutation || Date.now() - (record.savedAt || 0) <= DRAFT_TTL_MS) return record;
    await op("readwrite", store => store.delete(key));
    return null;
  });
}

/** Copy the newest live legacy draft atomically, retaining originals for recovery. Mark every
 * source as migrated so a successful submit/discard cannot resurrect an older account's draft.
 * Blobs, mutation receipts and savedAt are copied unchanged; migration does not extend the TTL. */
export function migrateDraft(key, matches) {
  return ordered(key, async () => {
    const db = await idb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite"), store = tx.objectStore(STORE);
      tx.oncomplete = resolve;
      tx.onabort = tx.onerror = () => reject(tx.error || new Error("초안 복원 실패"));
      const sources = [];
      let existing = null;
      const scan = store.openCursor();
      scan.onsuccess = () => {
        const cursor = scan.result;
        if (cursor) {
          const record = cursor.value;
          if (cursor.key === key) existing = record;
          else if (typeof cursor.key === "string" && matches(cursor.key) && !record.migratedTo
              && (record.preserveExpiredForMutation || Date.now() - (record.savedAt || 0) <= DRAFT_TTL_MS)) {
            sources.push({ key: cursor.key, record });
          }
          cursor.continue();
          return;
        }
        const pending = record => !!record?.preserveExpiredForMutation;
        let newest = existing;
        for (const source of sources) {
          const candidate = source.record;
          if (!pending(existing) && (!newest || (pending(candidate) && !pending(newest))
              || (pending(candidate) === pending(newest) && (candidate.savedAt || 0) > (newest.savedAt || 0)))) newest = candidate;
        }
        for (const source of sources) {
          // Other unresolved runs stay discoverable after this receipt is resolved/discarded.
          if (!pending(source.record) || source.record === newest)
            store.put({ ...source.record, migratedTo: key }, source.key);
        }
        if (newest && newest !== existing) {
          if (existing) store.put(existing, key + ":preserved:" + String(existing.savedAt || 0));
          store.put(newest, key);
        }
      };
    });
  });
}

let swept = false;
export async function purgeExpired() {
  if (swept) return;
  swept = true;
  try {
    const db = await idb();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE, "readwrite");
      transaction.oncomplete = resolve;
      transaction.onerror = transaction.onabort = () => reject(transaction.error);
      const request = transaction.objectStore(STORE).openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        if (!cursor.value.preserveExpiredForMutation
            && Date.now() - (cursor.value.savedAt || 0) > DRAFT_TTL_MS) cursor.delete();
        cursor.continue();
      };
    });
  } catch (error) { swept = false; throw error; }
}
