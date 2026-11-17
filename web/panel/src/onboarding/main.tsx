import { createRoot } from 'react-dom/client';
import { startPreferences } from '../preferences';
import { OnboardingPage } from './OnboardingPage';
import './onboarding.css';
startPreferences();
createRoot(document.getElementById('app')!).render(<OnboardingPage bridge={window.boxagentSetup} />);
