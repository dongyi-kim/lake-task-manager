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
    request.onerror = () => { connection = null; reject(request.error); };
  });
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
    if (Date.now() - (record.savedAt || 0) <= DRAFT_TTL_MS) return record;
    await op("readwrite", store => store.delete(key));
    return null;
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
        if (Date.now() - (cursor.value.savedAt || 0) > DRAFT_TTL_MS) cursor.delete();
        cursor.continue();
      };
    });
  } catch (error) { swept = false; throw error; }
}
