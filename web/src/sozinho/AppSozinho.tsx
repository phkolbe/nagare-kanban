/**
 * O Nagare fora da plataforma (decisao de 17/10/2026, N3): login local, shell proprio, produto
 * `nagare`, ingles por padrao. As telas sao as mesmas da plataforma, mais a administracao.
 * Quem pode tambem abre os produtos da Pampufa pelo seletor do trilho (18/10/2026).
 */

import { useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Carregando } from '@pampufa/ui'

import { caminhoEmIngles, ProvedorNagare } from '../app/contexto'
import { TelaAdmin, TelaMetricas, TelaSimulador } from '../app/telasSobDemanda'
import { ProvedorAvisos } from '../componentes/Avisos'
import { DicaDeSiglas } from '../componentes/DicaDeSiglas'
import { NaoEncontrada } from '../componentes/NaoEncontrada'
import { TelaProjeto } from '../features/projetos/TelaProjeto'
import { TelaProjetos } from '../features/projetos/TelaProjetos'
import { TelaQuadro } from '../features/quadro/TelaQuadro'
import { ProvedorIdioma } from '../i18n'
import { rotaDoLinkAntigo } from './linksAntigos'
import { ProvedorProdutoSozinho, useProdutoSozinho } from './produto'
import { ProvedorSessao, useSessao } from './sessao'
import { ShellSozinho } from './Shell'
import { TelaEntrar } from './TelaEntrar'

export function AppSozinho() {
  return (
    <ProvedorSessao>
      <ProvedorProdutoSozinho>
        <NagareDoProduto>
          <LinksAntigos />
          <Rotas />
        </NagareDoProduto>
      </ProvedorProdutoSozinho>
    </ProvedorSessao>
  )
}

/** As telas trabalham no produto escolhido no seletor do trilho. */
function NagareDoProduto({ children }: { children: ReactNode }) {
  const { produto } = useProdutoSozinho()
  return (
    <ProvedorNagare modo="sozinho" produto={produto} nomeProduto="Nagare">
      {children}
    </ProvedorNagare>
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
        <Route path="/sign-in" element={<TelaEntrar />} />
        <Route path="*" element={<Navigate to="/sign-in" replace state={{ de: local.pathname }} />} />
      </Routes>
    )
  }
  return (
    <ProvedorIdioma>
      <ProvedorAvisos>
        <Routes>
          <Route path="/sign-in" element={<Navigate to="/board" replace />} />
          <Route element={<ShellSozinho />}>
            <Route index element={<Navigate to="board" replace />} />
            <Route path="board" element={<TelaQuadro />} />
            <Route path="board/:quadroId" element={<TelaQuadro />} />
            <Route path="projects" element={<TelaProjetos />} />
            <Route path="projects/:projetoId" element={<TelaProjeto />} />
            <Route path="metrics" element={<TelaMetricas />} />
            <Route path="metrics/:quadroId" element={<TelaMetricas />} />
            <Route path="simulator" element={<TelaSimulador />} />
            <Route path="simulator/:quadroId" element={<TelaSimulador />} />
            <Route path="admin" element={<Navigate to="users" replace />} />
            <Route path="admin/:aba" element={<TelaAdmin />} />
            {/* Os nomes em portugues de antes de 19/10/2026 levam ao nome novo. */}
            {['quadro', 'projetos', 'metricas', 'simulador', 'entrar'].map((antigo) => (
              <Route key={antigo} path={`${antigo}/*`} element={<RotaAntiga />} />
            ))}
            <Route path="*" element={<NaoEncontrada />} />
          </Route>
        </Routes>
        <DicaDeSiglas />
      </ProvedorAvisos>
    </ProvedorIdioma>
  )
}

/** Um endereco com o nome antigo (`/nagare/quadro/1`) vai para o novo (`/nagare/board/1`). */
function RotaAntiga() {
  const local = useLocation()
  return <Navigate to={`${caminhoEmIngles(local.pathname)}${local.search}${local.hash}`} replace />
}
