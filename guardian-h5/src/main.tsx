import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { ConfigProvider } from 'antd-mobile';
import { App } from './app/App';
import { SessionBoundary } from './state/SessionBoundary';
import './styles/globals.css';
import './styles/forms.css';

const container = document.getElementById('root');
if (!container) throw new Error('页面缺少 root 容器');
const router = createBrowserRouter([{ path: '*', element: <App /> }]);

createRoot(container).render(
  <StrictMode>
    <ConfigProvider>
      <SessionBoundary><RouterProvider router={router} /></SessionBoundary>
    </ConfigProvider>
  </StrictMode>,
);
