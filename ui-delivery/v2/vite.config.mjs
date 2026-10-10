import { fileURLToPath } from 'node:url';
const local = path => fileURLToPath(new URL(path, import.meta.url));
export default {
  root: local('./'),
  base: './',
  resolve: { alias: {
    'react-dom': local('../../guardian-h5/node_modules/react-dom'),
    react: local('../../guardian-h5/node_modules/react'),
    'lucide-react': local('../../guardian-h5/node_modules/lucide-react/dist/esm/lucide-react.mjs'),
  } },
  server: { host: '127.0.0.1', port: 5188, strictPort: true, hmr: false, fs: { allow: [local('../../')] } },
  build: { outDir: local('../../.ui-work/v2-build'), emptyOutDir: true },
};
