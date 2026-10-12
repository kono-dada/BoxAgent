import { createRoot } from 'react-dom/client';
import { App } from './App';
import { startPreferences } from './preferences';
startPreferences();
createRoot(document.getElementById('app')!).render(<App bridge={window.boxagent} />);
