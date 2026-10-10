import { fileURLToPath } from 'node:url';

const local = (path) => fileURLToPath(new URL(path, import.meta.url));
export default {
  root: local('./'),
  base: './',
  resolve: { alias: {
    'react-dom': local('../guardian-h5/node_modules/react-dom'),
    'react': local('../guardian-h5/node_modules/react'),
    'lucide-react': local('../guardian-h5/node_modules/lucide-react/dist/esm/lucide-react.mjs'),
  } },
  server: { host: '127.0.0.1', port: 5186, strictPort: true, hmr: false },
  build: { outDir: 'preview-build', emptyOutDir: true },
};
