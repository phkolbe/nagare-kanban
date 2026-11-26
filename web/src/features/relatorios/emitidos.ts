/**
 * Parte pura da onda 2 dos Reports (25/11/2026): relatório emitido, link de cliente, página
 * pública e o atalho do "Registrar previsão" para o REP-09. Conferida por `checks/telas.check.ts`.
 *
 * - O emitido guarda o Documento inteiro: reaberto, mostra o que foi emitido, mesmo que o quadro
 *   tenha mudado (a faixa "Emitido em … por …" deixa claro que não é o ao vivo).
 * - O link de cliente só sai de emitidos do REP-02 e do REP-03 filtrado por projetos; vale 7 dias
 *   por padrão e o endereço completo aparece uma vez só, ao criar (o banco guarda só o hash).
 */

import type { Idioma } from '../../api/tipos'
import type { Documento, Parametros } from './formato'

export interface PessoaResumo {
  id: string
  name: string
}

export type SituacaoDoLink = 'active' | 'expired' | 'revoked'

export interface LinkDeCliente {
  id: string
  issue_id: string
  projects: { id: string; name: string }[]
  expires_at: string
  created_at: string
  created_by: PessoaResumo | null
  revoked_at: string | null
  revoked_by: PessoaResumo | null
  access_count: number
  last_access_at: string | null
  state: SituacaoDoLink
  /** só na resposta da criação: o token, que não volta nunca mais */
  token?: string
  path?: string
}

export interface Emitido {
  id: string
  product_id: string
  template: string
  code: string
  title: string
  subtitle: string | null
  params: Record<string, unknown>
  period: { kind: string; from: string; to: string; label: string | null }
  format_version: number
  locale: Idioma
  issued_at: string
  issued_by: PessoaResumo | null
  /** pode virar link de cliente (REP-02, ou REP-03 com projetos escolhidos) */
  shareable: boolean
  active_links?: number
  document?: Documento
  /** só para quem pode criar link (`share_reports`) */
  links?: LinkDeCliente[]
}

export interface RelatorioPublico {
  document: Documento
  issued_at: string
  issued_by_name: string | null
  expires_at: string
  locale: Idioma
}

/** Os relatórios que podem ir para o cliente (decisão R6). */
export const MODELOS_COM_LINK: readonly string[] = ['project_status', 'hours']
/** As validades oferecidas ao criar o link, em dias (R19: 7 por padrão; até 90). */
export const OPCOES_DE_VALIDADE: readonly number[] = [1, 3, 7, 14, 30, 90]
export const VALIDADE_PADRAO = 7

/** O corpo do `POST /reports/issued`: o modelo e os mesmos parâmetros do relatório na tela. */
export function corpoDaEmissao(modelo: string, p: Parametros): Record<string, string> {
  const corpo: Record<string, string> = { template: modelo, period: p.period }
  for (const chave of ['date', 'from', 'to', 'board_id', 'project_id', 'project_ids'] as const) {
    const valor = p[chave]
    if (valor) corpo[chave] = valor
  }
  return corpo
}

/** O endereço que vai para o cliente: `https://servidor/nagare/r/<token>`. */
export function enderecoDoLink(origem: string, base: string, token: string): string {
  return `${origem.replace(/\/+$/, '')}${base}/r/${encodeURIComponent(token)}`
}

export type SituacaoDaPaginaPublica = 'vencido' | 'revogado' | 'inexistente' | 'limite' | 'erro'

/** O que dizer ao cliente quando a página pública não abre, pela resposta da API. */
export function situacaoDoErroPublico(status: number, codigo: string): SituacaoDaPaginaPublica {
  if (codigo === 'report_link_expired') return 'vencido'
  if (codigo === 'report_link_revoked') return 'revogado'
  if (status === 404 || codigo === 'report_link_not_found') return 'inexistente'
  if (status === 429) return 'limite'
  return 'erro'
}

function somarDias(dia: string, dias: number): string {
  const d = new Date(`${dia}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + dias)
  return d.toISOString().slice(0, 10)
}

/**
 * O REP-09 que mostra a previsão recém-registrada: o quadro dela e os últimos 30 dias até hoje
 * (a semana e o mês padrão são os últimos completos, que ainda não têm a previsão de hoje).
 */
export function parametrosDoRep09(quadroId: string, hoje: string): Parametros {
  return { period: 'custom', from: somarDias(hoje, -29), to: hoje, board_id: quadroId }
}

/**
 * A língua da página do cliente enquanto o relatório não chega (depois vale a do emitido): a
 * última usada neste navegador no Nagare, senão a do navegador.
 */
export function idiomaDoNavegador(guardado: string | null, navegador: string | undefined): Idioma {
  if (guardado === 'pt-BR' || guardado === 'en') return guardado
  return (navegador ?? '').toLowerCase().startsWith('pt') ? 'pt-BR' : 'en'
}

/** Quantos links ainda abrem. */
export function linksAtivos(links: Pick<LinkDeCliente, 'state'>[]): number {
  return links.filter((l) => l.state === 'active').length
}
