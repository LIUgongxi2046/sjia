import { createRoot } from 'react-dom/client';
import { DesignSpecimen } from './DesignSpecimen';

// DEV entry only; never mounted by App or the production index build.
// No AccessProvider, session, private API, clinical approval or authorization injection.
if (import.meta.env.DEV) {
  const params = new URLSearchParams(location.search);
  const longCopy = params.get('long') === '1', initialPanel = params.get('panel') === '1';
  document.documentElement.dataset.visualSpecimen = initialPanel ? 'TW-PANEL-design-only-not-clinical-evidence' : longCopy ? 'LONG-COPY-layout-only-not-clinical-evidence' : 'S03-design-only-not-clinical-evidence';
  createRoot(document.getElementById('root')!).render(<DesignSpecimen longCopy={longCopy} initialPanel={initialPanel} externallyLabelled={window.frameElement?.getAttribute('data-specimen-labelled') === 'true'} />);
}
