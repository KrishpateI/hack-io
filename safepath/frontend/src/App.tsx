import { useEffect, useState } from 'react';
import { OfficialLinks } from './components/OfficialLinks';
import { RootLayout } from './components/RootLayout';
import SourcesLimitations from './pages/SourcesLimitations';

type Page = 'map' | 'sources';
const fromHash = (): Page => (window.location.hash === '#/sources' ? 'sources' : 'map');

export default function App() {
  const [page, setPage] = useState<Page>(fromHash);
  useEffect(() => {
    const on = () => setPage(fromHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);

  return (
    <RootLayout>
      <div className="flex flex-col md:flex-row">
        <aside aria-label="Sidebar" className="border-b border-border-subtle md:w-56 md:border-b-0 md:border-r">
          <nav aria-label="Main" className="flex gap-3 p-3 text-sm">
            <a href="#/" className="text-route underline" aria-current={page === 'map' ? 'page' : undefined}>Map</a>
            <a href="#/sources" className="text-route underline" aria-current={page === 'sources' ? 'page' : undefined}>
              Data sources and limitations
            </a>
          </nav>
          <OfficialLinks />
        </aside>
        <div className="min-w-0 flex-1">
          {page === 'sources' ? <SourcesLimitations /> : <p className="p-3 text-text-muted">Map view coming soon.</p>}
        </div>
      </div>
    </RootLayout>
  );
}
