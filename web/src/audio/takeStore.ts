/**
 * Дубль переживает перезагрузку вкладки.
 *
 * До сих пор запись жила только в памяти: `beforeunload` предупреждал, но
 * случайный Ctrl+R, вылетевшая вкладка или разрядившийся ноутбук уносили
 * спетое целиком. Здесь дубль складывается в IndexedDB — единственное
 * хранилище браузера, которое берёт десятки мегабайт без разговоров.
 *
 * Хранится один дубль на трек: два — это уже история дублей, а её никто не
 * просил, и место она ест мегабайтами.
 */

import type { Samples } from "./samples";

const DB_NAME = "karaoke";
const DB_VERSION = 1;
const STORE = "takes";

export interface StoredTake {
  channels: Samples[];
  sampleRate: number;
  savedAt: number;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function run<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return open().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const transaction = db.transaction(STORE, mode);
        const request = work(transaction.objectStore(STORE));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        transaction.oncomplete = () => db.close();
      }),
  );
}

/**
 * Сохраняет дубль. Никогда не бросает наружу.
 *
 * Место в хранилище может кончиться, приватный режим может запретить запись —
 * ни то, ни другое не повод ронять студию: дубль в памяти всё ещё цел, и
 * человек об этом даже не узнает.
 */
export async function saveTake(
  trackId: string,
  channels: Samples[],
  sampleRate: number,
): Promise<boolean> {
  try {
    await run("readwrite", (store) =>
      store.put({ channels, sampleRate, savedAt: Date.now() }, trackId),
    );
    return true;
  } catch {
    return false;
  }
}

export async function loadTake(trackId: string): Promise<StoredTake | null> {
  try {
    const stored = await run<StoredTake | undefined>("readonly", (store) =>
      store.get(trackId),
    );
    if (!stored || !stored.channels?.length) return null;
    return stored;
  } catch {
    return null;
  }
}

export async function clearTake(trackId: string): Promise<void> {
  try {
    await run("readwrite", (store) => store.delete(trackId));
  } catch {
    // Не удалось убрать — переживём: следующий дубль перезапишет запись.
  }
}
