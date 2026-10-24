/**
 * Minimal key-value store over IndexedDB (hand-rolled to avoid a dependency).
 * Falls back to an in-memory Map when IndexedDB is unavailable (private mode, tests), so callers
 * never need to branch.
 */
export interface KeyValueStore {
  get<T>(key: string): Promise<T | undefined>;
  set<T>(key: string, value: T): Promise<void>;
  del(key: string): Promise<void>;
}

export function memoryStore(): KeyValueStore {
  const m = new Map<string, unknown>();
  return {
    get: <T,>(k: string) => Promise.resolve(m.get(k) as T | undefined),
    set: (k, v) => {
      m.set(k, structuredClone(v));
      return Promise.resolve();
    },
    del: (k) => {
      m.delete(k);
      return Promise.resolve();
    },
  };
}

function open(dbName: string, storeName: string): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(dbName, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(storeName);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'));
  });
}

export function idbStore(dbName = 'safepath', storeName = 'kv'): KeyValueStore {
  if (typeof indexedDB === 'undefined') return memoryStore();
  let db: Promise<IDBDatabase> | null = null;
  const run = async <T,>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    db ??= open(dbName, storeName);
    let conn: IDBDatabase;
    try {
      conn = await db;
    } catch (e) {
      db = null;
      throw e;
    }
    return new Promise<T>((resolve, reject) => {
      const tx = conn.transaction(storeName, mode);
      const req = fn(tx.objectStore(storeName));
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = () => reject(tx.error ?? new Error('IndexedDB transaction failed'));
      tx.onabort = () => reject(tx.error ?? new Error('IndexedDB transaction aborted'));
    });
  };
  return {
    get: <T,>(k: string) => run<T | undefined>('readonly', (s) => s.get(k) as IDBRequest<T | undefined>),
    set: async (k, v) => {
      await run('readwrite', (s) => s.put(v, k));
    },
    del: async (k) => {
      await run('readwrite', (s) => s.delete(k));
    },
  };
}
