import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { initTheme } from './theme';
import './styles.css';

initTheme(); // apply persisted light/dark before first paint (no flash)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
