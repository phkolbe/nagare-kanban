/**
 * Os dois modos das telas do Nagare (decisao de 17/10/2026, N3):
 *
 * - `plataforma`: dentro da Pampufa, em `/grupo-sr/<produto>/tarefas/...`, com o Shell, o login
 *   unico e o token do `@pampufa/ui`; a API atras do gateway em `/nagare/api/v1`.
 * - `sozinho`: o Nagare fora da plataforma (`:8770/` ou `/nagare/` no gateway), com shell e
 *   login locais, produto `nagare` e a API no mesmo servidor (`/api/v1`).
 *
 * As telas nao sabem em que modo estao: pedem o produto e os caminhos a este contexto.
 */

import { createContext, useContext, type ReactNode } from 'react'

export type Modo = 'plataforma' | 'sozinho'

/** O produto do Nagare fora da plataforma. */
export const PRODUTO_SOZINHO = 'nagare'

/** O modo vem do caminho em que a pagina abriu (o mesmo build serve os dois). */
export function modoDoCaminho(caminho: string): Modo {
  return caminho === '/grupo-sr' || caminho.startsWith('/grupo-sr/') ? 'plataforma' : 'sozinho'
}

/** Fora da plataforma o Nagare abre na raiz (`:8770/`) ou atras do gateway (`/nagare/`). */
export function baseDoSozinho(caminho: string): string {
  return caminho === '/nagare' || caminho.startsWith('/nagare/') ? '/nagare' : ''
}

export const MODO: Modo = typeof window === 'undefined' ? 'sozinho' : modoDoCaminho(window.location.pathname)
export const BASE_SOZINHO = typeof window === 'undefined' ? '' : baseDoSozinho(window.location.pathname)

export interface ContextoNagare {
  modo: Modo
  /** Produto da API (`X-Produto`). */
  produto: string
  nomeProduto: string
  /** Caminho de uma tela do Nagare para o roteador: `quadro/123` -> `/sr-adv/tarefas/quadro/123`. */
  rota: (caminho: string) => string
}

/** As rotas das telas, nos dois modos (sem a base do roteador). */
export function montarRota(modo: Modo, produto: string, caminho: string): string {
  const limpo = caminho.replace(/^\/+/, '')
  return modo === 'plataforma' ? `/${produto}/tarefas/${limpo}`.replace(/\/$/, '') : `/${limpo}`.replace(/\/$/, '') || '/'
}

const Contexto = createContext<ContextoNagare | null>(null)

export function ProvedorNagare({
  modo,
  produto,
  nomeProduto,
  children,
}: {
  modo: Modo
  produto: string
  nomeProduto: string
  children: ReactNode
}) {
  const valor: ContextoNagare = { modo, produto, nomeProduto, rota: (c) => montarRota(modo, produto, c) }
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>
}

export function useNagare(): ContextoNagare {
  const contexto = useContext(Contexto)
  if (!contexto) throw new Error('useNagare fora do ProvedorNagare')
  return contexto
}
