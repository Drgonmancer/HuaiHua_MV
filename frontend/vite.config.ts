import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 产物固定文件名进入 backend/static/canvas/，由 Flask 模板直接引用。
export default defineConfig({
  plugins: [react()],
  base: '/static/canvas/',
  build: {
    outDir: '../backend/static/canvas',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'canvas-app.js',
        assetFileNames: 'canvas-app.[ext]',
        chunkFileNames: 'canvas-chunk-[name].js',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:5009', changeOrigin: false },
      '/static/uploads': { target: 'http://localhost:5009', changeOrigin: false },
      '/studio-media': { target: 'http://localhost:5009', changeOrigin: false },
      '/canvas-assets': { target: 'http://localhost:5009', changeOrigin: false },
      '/static/brand': { target: 'http://localhost:5009', changeOrigin: false },
    },
  },
})
