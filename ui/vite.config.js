import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The build goes to ui/dist, which ships in the repo so nobody needs Node to run the studio.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 900,
           rollupOptions: { output: { entryFileNames: 'assets/app.js', chunkFileNames: 'assets/[name].js', assetFileNames: 'assets/[name][extname]' } } },
  server: { proxy: { '/api': 'http://localhost:8770' } },
});
