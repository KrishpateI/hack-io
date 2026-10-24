import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { bootstrapDemoUser } from './components/demo/demoSession';
import { installOfflineLayerCache } from './services/offlineLayers';
import { getOfflineManager } from './services/offline';
import { registerServiceWorker } from './sw/register';
import './styles/tailwind.css';

installOfflineLayerCache(getOfflineManager());
registerServiceWorker();

// ?demoUser=A|B|C obtains that SIMULATED user's token first; otherwise the app starts a guest session.
void bootstrapDemoUser().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
