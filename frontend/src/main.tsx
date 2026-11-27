import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { canonicalEntryHref } from './workspaceRoutes';
import '@fontsource-variable/inter/wght.css';
import '@fontsource-variable/inter/wght-italic.css';
import './styles.css';
import './stage.css';
import './layout.css';
import './readability.css';
import './dialogs.css';
import './workspace.css';
import './reference.css';
import './alignment-fixes.css';
import './dynamic-workspace.css';
import './intake.css';
import './workflow-shell.css';
import './account.css';
import './dashboard.css';
import './intake-review.css';

const entryHref = canonicalEntryHref(window.location);
if (entryHref) window.history.replaceState(window.history.state, '', entryHref);

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
