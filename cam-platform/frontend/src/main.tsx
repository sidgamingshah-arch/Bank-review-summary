import React from 'react';
import ReactDOM from 'react-dom/client';
// Self-hosted fonts (bundled by Vite — no runtime network, safe for the
// bank's offline deployment). Archivo = headings/brand; IBM Plex Sans = UI
// body; IBM Plex Mono = codes/IDs/figures.
import '@fontsource/archivo/500.css';
import '@fontsource/archivo/600.css';
import '@fontsource/archivo/700.css';
import '@fontsource/archivo/800.css';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-sans/700.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-mono/600.css';
import { App } from './App';
import { initTheme } from './theme';
import './styles.css';

initTheme(); // apply persisted light/dark before first paint (no flash)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
