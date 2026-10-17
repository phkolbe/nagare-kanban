/**
 * O Nagare fora da plataforma (decisao de 17/10/2026, N3): login local, shell proprio, produto
 * `nagare`, ingles por padrao. As telas sao as mesmas da plataforma, mais a administracao.
 */

import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Carregando } from '@pampufa/ui'

import { PRODUTO_SOZINHO, ProvedorNagare } from '../app/contexto'
import { TelaAdmin, TelaMetricas, TelaSimulador } from '../app/telasSobDemanda'
import { ProvedorAvisos } from '../componentes/Avisos'
import { NaoEncontrada } from '../componentes/NaoEncontrada'
import { TelaProjeto } from '../features/projetos/TelaProjeto'
import { TelaProjetos } from '../features/projetos/TelaProjetos'
import { TelaQuadro } from '../features/quadro/TelaQuadro'
import { ProvedorIdioma } from '../i18n'
import { rotaDoLinkAntigo } from './linksAntigos'
import { ProvedorSessao, useSessao } from './sessao'
import { ShellSozinho } from './Shell'
import { TelaEntrar } from './TelaEntrar'

export function AppSozinho() {
  return (
    <ProvedorSessao>
      <ProvedorNagare modo="sozinho" produto={PRODUTO_SOZINHO} nomeProduto="Nagare">
        <LinksAntigos />
        <Rotas />
      </ProvedorNagare>
    </ProvedorSessao>
  )
}

/** Os links da tela antiga (`#/board/<id>`) continuam abrindo a tela certa. */
function LinksAntigos() {
  const navegar = useNavigate()
  useEffect(() => {
    const destino = rotaDoLinkAntigo(window.location.hash)
    if (destino) navegar(destino, { replace: true })
  }, [navegar])
  return null
}

function Rotas() {
  const { estado } = useSessao()
  const local = useLocation()
  if (estado.situacao === 'carregando') return <Carregando texto="…" />
  if (estado.situacao === 'anonimo') {
    return (
      <Routes>
        <Route path="/entrar" element={<TelaEntrar />} />
        <Route path="*" element={<Navigate to="/entrar" replace state={{ de: local.pathname }} />} />
      </Routes>
    )
  }
  return (
    <ProvedorIdioma>
      <ProvedorAvisos>
        <Routes>
          <Route path="/entrar" element={<Navigate to="/quadro" replace />} />
          <Route element={<ShellSozinho />}>
            <Route index element={<Navigate to="quadro" replace />} />
            <Route path="quadro" element={<TelaQuadro />} />
            <Route path="quadro/:quadroId" element={<TelaQuadro />} />
            <Route path="projetos" element={<TelaProjetos />} />
            <Route path="projetos/:projetoId" element={<TelaProjeto />} />
            <Route path="metricas" element={<TelaMetricas />} />
            <Route path="metricas/:quadroId" element={<TelaMetricas />} />
            <Route path="simulador" element={<TelaSimulador />} />
            <Route path="simulador/:quadroId" element={<TelaSimulador />} />
            <Route path="admin" element={<Navigate to="usuarios" replace />} />
            <Route path="admin/:aba" element={<TelaAdmin />} />
            <Route path="*" element={<NaoEncontrada />} />
          </Route>
        </Routes>
      </ProvedorAvisos>
    </ProvedorIdioma>
  )
}
