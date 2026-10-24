import { setLayerFetchObserver, setOfflineLayerStore } from '../maps/layers/layerData';
import type { FC } from '../maps/layers/types';
import { idbStore, type KeyValueStore } from './idb';
import type { OfflineManager } from './offline';

interface Entry {
  data: FC;
  savedAt: number;
}

/**
 * Wire static map layers to the Offline_Manager: keep the last good copy in IndexedDB (used when
 * the network fails and no service worker answered), and tell the manager whether a layer is live
 * or cached so the legend can say so.
 */
export function installOfflineLayerCache(
  manager: OfflineManager,
  store: KeyValueStore = idbStore('safepath-offline-layers', 'layers'),
  now: () => number = Date.now,
): void {
  setOfflineLayerStore({
    async get(key) {
      const e = await store.get<Entry>(key);
      if (!e) return null;
      manager.noteLayer(key, e.savedAt, true);
      return e.data;
    },
    async put(key, data) {
      // A copy the service worker served from its cache is not newer than what we hold.
      if (manager.isLayerCached(key)) return;
      await store.set<Entry>(key, { data, savedAt: now() });
    },
  });
  setLayerFetchObserver((key, headers) => {
    if (headers.get('x-sp-source') === 'cache') {
      const at = Date.parse(headers.get('x-sp-cached-at') ?? '');
      manager.noteLayer(key, Number.isNaN(at) ? null : at, true);
    } else {
      manager.noteLayer(key, null, false);
    }
  });
}
