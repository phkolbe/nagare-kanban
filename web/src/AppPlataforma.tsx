import { useState } from 'react'
import { Navigate, Outlet, Route, Routes, useParams } from 'react-router-dom'
import { Carregando, EmConstrucao, PRODUTO_PAMPUFA, RotaDeProduto, useAuth, useProduto } from '@pampufa/ui'

import { ProvedorNagare } from './app/contexto'
import { TelaMetricas, TelaSimulador } from './app/telasSobDemanda'
import { AjudaDeAtalhos, useAtalhos } from './componentes/Atalhos'
import { ProvedorAvisos } from './componentes/Avisos'
import { NaoEncontrada } from './componentes/NaoEncontrada'
import { TelaConsolidado } from './features/consolidado/TelaConsolidado'
import { TelaProjeto } from './features/projetos/TelaProjeto'
import { TelaProjetos } from './features/projetos/TelaProjetos'
import { TelaQuadro } from './features/quadro/TelaQuadro'
import { TelaRegras } from './features/regras/TelaRegras'
import { ProvedorIdioma } from './i18n'

/**
 * Telas React do Nagare Kanban no modulo Tarefas (NG7, N2): `/grupo-sr/<produto>/tarefas/...`.
 * O Nagare e um sistema a parte, so por API (NG1); o menu, o sino e o login sao da plataforma.
 */
export function AppPlataforma() {
  const { estado } = useAuth()
  // Sem sessao o ProvedorAuth ja mandou para /grupo-sr/entrar.
  if (estado.situacao !== 'autenticado') return <Carregando texto="…" />
  return (
    <Routes>
      <Route path="/:produto" element={<RotaDeProduto sistema="nagare" />}>
        <Route element={<ComIdioma />}>
          <Route index element={<Navigate to="tarefas" replace />} />
          <Route path="tarefas" element={<InicioDasTarefas />} />
          <Route path="tarefas/quadro" element={<TelaQuadro />} />
          <Route path="tarefas/quadro/:quadroId" element={<TelaQuadro />} />
          <Route path="tarefas/projetos" element={<TelaProjetos />} />
          <Route path="tarefas/projetos/:projetoId" element={<TelaProjeto />} />
          <Route path="tarefas/metricas" element={<TelaMetricas />} />
          <Route path="tarefas/metricas/:quadroId" element={<TelaMetricas />} />
          <Route path="tarefas/simulador" element={<TelaSimulador />} />
          <Route path="tarefas/simulador/:quadroId" element={<TelaSimulador />} />
          <Route path="tarefas/regras" element={<TelaRegras />} />
          <Route path="tarefas/*" element={<NaoEncontrada />} />
          <Route path="*" element={<EmConstrucao />} />
        </Route>
      </Route>
    </Routes>
  )
}

function ComIdioma() {
  const produto = useProduto()
  return (
    <ProvedorNagare modo="plataforma" produto={produto.id} nomeProduto={produto.nome}>
      <ProvedorIdioma>
        <ProvedorAvisos>
          <ComAtalhos />
        </ProvedorAvisos>
      </ProvedorIdioma>
    </ProvedorNagare>
  )
}

function ComAtalhos() {
  const [ajuda, setAjuda] = useState(false)
  useAtalhos({ abrirAjuda: () => setAjuda(true) })
  return (
    <>
      <Outlet />
      {ajuda && <AjudaDeAtalhos fechar={() => setAjuda(false)} />}
    </>
  )
}

/** Na Pampufa, a visao consolidada da PO; num produto, o quadro dele. */
function InicioDasTarefas() {
  const { produto } = useParams()
  if (produto === PRODUTO_PAMPUFA) return <TelaConsolidado />
  return <Navigate to="quadro" replace />
}
