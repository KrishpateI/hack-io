import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

/**
 * Fills the placeholders in dist/sw.js after the build: the list of hashed assets to precache and
 * a build id derived from them, so a new deploy gets a new shell cache and old caches are removed.
 */
export default function serviceWorkerManifest() {
  let outDir = 'dist';
  let root = process.cwd();
  const emitted = [];
  return {
    name: 'safepath-sw-manifest',
    apply: 'build',
    configResolved(cfg) {
      outDir = cfg.build.outDir;
      root = cfg.root;
    },
    generateBundle(_o, bundle) {
      emitted.length = 0;
      for (const name of Object.keys(bundle)) {
        if (/\.(js|css|woff2?|svg|png|webmanifest)$/.test(name)) emitted.push(`/${name}`);
      }
    },
    closeBundle() {
      const file = path.resolve(root, outDir, 'sw.js');
      if (!existsSync(file)) return;
      const urls = ['/', '/index.html', ...emitted.sort()];
      const publicExtras = ['/manifest.webmanifest', '/favicon.svg'].filter((u) => existsSync(path.resolve(root, outDir, u.slice(1))));
      const list = [...new Set([...urls, ...publicExtras])];
      const id = createHash('sha256').update(list.join('\n')).digest('hex').slice(0, 12);
      let src = readFileSync(file, 'utf8');
      src = src.replace(/const PRECACHE = .*\/\*__PRECACHE__\*\/;/, `const PRECACHE = ${JSON.stringify(list)};`);
      src = src.replace(/'dev' \/\*__BUILD_ID__\*\//, `'${id}'`);
      writeFileSync(file, src);
    },
  };
}

