import '@pampufa/ui/estilos.css'
import './nagare.css'
import './nagare-n3.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { criarQueryClient, montarAplicacao } from '@pampufa/ui'

import { AppPlataforma } from './AppPlataforma'
import { BASE_SOZINHO, MODO } from './app/contexto'
import { AppSozinho } from './sozinho/AppSozinho'
import { aplicarTema, temaGuardado } from './sozinho/tema'

// Um build, dois modos (N3): dentro da Pampufa a montagem e a do @pampufa/ui (login unico,
// /grupo-sr); fora dela, o Nagare monta o proprio roteador, a sessao local e o tema.
if (MODO === 'plataforma') {
  montarAplicacao(<AppPlataforma />)
} else {
  aplicarTema(temaGuardado())
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={criarQueryClient()}>
        <BrowserRouter basename={BASE_SOZINHO || '/'}>
          <AppSozinho />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  )
}
