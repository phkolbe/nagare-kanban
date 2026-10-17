import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Telas do modulo Tarefas em /grupo-sr/_nagare/ (o Nagare as serve; o gateway roteia, NG7).
// Em `npm run dev`, a API (/nagare/api/v1) e a autenticacao (/api) vao para o gateway (:8080).
const GATEWAY = 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react()],
  base: '/grupo-sr/_nagare/',
  resolve: {
    // @pampufa/ui pode vir de pasta local: uma copia so de cada biblioteca.
    dedupe: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
  },
  server: {
    port: 5177,
    proxy: { '/api': { target: GATEWAY }, '/nagare': { target: GATEWAY } },
  },
})
