/**
 * Cliente da API do Nagare, nos dois modos (ver `app/contexto.tsx`).
 *
 * - Na plataforma (NG1): a API fica atras do gateway em `/nagare/api/v1`, com o MESMO token da
 *   Pampufa (login unico, NG5) e o produto em `X-Produto`. Token, renovacao e problem+json vem
 *   do `@pampufa/ui` (o Nagare responde `codigo` e `title`).
 * - Sozinho: a API no proprio servidor, com o token do login local (`sessaoLocal.ts`).
 */

import { requisitar, type Opcoes } from '@pampufa/ui'

import { MODO } from '../app/contexto'
import { requisitarLocal } from './sessaoLocal'

/** Onde o gateway publica a API do Nagare (prefixo retirado, ver `infra/Caddyfile`). */
export const API_NAGARE = '/nagare/api/v1'

export function nagare<T>(produto: string, caminho: string, opcoes: Opcoes = {}): Promise<T> {
  const comProduto = { ...opcoes, cabecalhos: { 'X-Produto': produto, ...opcoes.cabecalhos } }
  if (MODO === 'sozinho') return requisitarLocal<T>(caminho, comProduto)
  return requisitar<T>(`${API_NAGARE}${caminho}`, comProduto)
}

/** Monta `?a=1&b=2` sem os valores vazios. */
export function consulta(parametros: Record<string, string | number | boolean | null | undefined>): string {
  const pares = Object.entries(parametros).filter(
    (par): par is [string, string | number | boolean] => par[1] !== undefined && par[1] !== null && par[1] !== '',
  )
  if (pares.length === 0) return ''
  return `?${new URLSearchParams(pares.map(([k, v]) => [k, String(v)])).toString()}`
}

/** Chaves do cache por produto (o mesmo cartao nunca aparece em dois produtos). */
export const chaves = {
  eu: (produto: string) => ['nagare', produto, 'eu'] as const,
  quadros: (produto: string) => ['nagare', produto, 'quadros'] as const,
  quadro: (produto: string, id: string) => ['nagare', produto, 'quadro', id] as const,
  visao: (produto: string, id: string, filtros: string) => ['nagare', produto, 'visao', id, filtros] as const,
  visoes: (produto: string, id: string) => ['nagare', produto, 'visao', id] as const,
  cartao: (produto: string, id: string) => ['nagare', produto, 'cartao', id] as const,
  historico: (produto: string, id: string) => ['nagare', produto, 'cartao', id, 'historico'] as const,
  projetos: (produto: string) => ['nagare', produto, 'projetos'] as const,
  projeto: (produto: string, id: string) => ['nagare', produto, 'projeto', id] as const,
  pessoas: (produto: string) => ['nagare', produto, 'pessoas'] as const,
  vinculo: (produto: string) => ['nagare', produto, 'vinculo'] as const,
  modelos: (produto: string) => ['nagare', produto, 'modelos'] as const,
  consolidado: () => ['nagare', 'pampufa', 'consolidado'] as const,
  regras: (produto: string) => ['pampufa', produto, 'nagare', 'regras'] as const,
}

/** Regras por tipo de providencia: API do pampufa, dono da ponte (NG9). */
export function urlRegras(produto: string): string {
  return `/api/v1/p/${encodeURIComponent(produto)}/pampufa/nagare/regras`
}
