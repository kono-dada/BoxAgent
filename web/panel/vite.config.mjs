import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ base: './', plugins: [react()], build: {
  outDir: 'dist', emptyOutDir: true,
  rollupOptions: { input: ['index.html','companion.html','pet.html','tasks.html','camera.html'], output: { manualChunks: { graph: ['vis-network/standalone'], react: ['react', 'react-dom'] } } }
} });
