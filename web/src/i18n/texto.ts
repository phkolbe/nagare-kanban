/** Parte pura da internacionalizacao (sem React): textos, formatos e erros pelo codigo. */

import type { Idioma } from '../api/tipos'
import { CATALOGOS, type Chave } from './mensagens'

type Variaveis = Record<string, string | number>

export interface Traducao {
  idioma: Idioma
  t: (chave: Chave, variaveis?: Variaveis) => string
  /** Data de negocio (`2026-10-17`) ou instante ISO, so a data. */
  data: (iso: string | null | undefined) => string
  dataHora: (iso: string | null | undefined) => string
  numero: (valor: number, casas?: number) => string
}

/** Troca `{nome}` pelos valores (sem HTML: o React escapa). */
export function interpolar(texto: string, variaveis?: Variaveis): string {
  if (!variaveis) return texto
  return texto.replace(/\{(\w+)\}/g, (inteiro, nome: string) =>
    nome in variaveis ? String(variaveis[nome]) : inteiro,
  )
}

export function traducao(idioma: Idioma): Traducao {
  const catalogo = CATALOGOS[idioma]
  const formatoData = new Intl.DateTimeFormat(idioma, { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' })
  const formatoDataHora = new Intl.DateTimeFormat(idioma, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
  return {
    idioma,
    t: (chave, variaveis) => interpolar(catalogo[chave], variaveis),
    data: (iso) => {
      if (!iso) return '—'
      const dia = /^\d{4}-\d{2}-\d{2}/.exec(iso)?.[0]
      return dia ? formatoData.format(new Date(`${dia}T00:00:00Z`)) : '—'
    },
    dataHora: (iso) => (iso ? formatoDataHora.format(new Date(iso)) : '—'),
    numero: (valor, casas = 0) =>
      new Intl.NumberFormat(idioma, { minimumFractionDigits: casas, maximumFractionDigits: casas }).format(valor),
  }
}

/**
 * A chave do texto de um erro de API pelo codigo (`flow_locked` -> `erro.flow_locked`), ou pela
 * situacao HTTP. `null`: sem traducao, vale a mensagem do servidor.
 */
export function chaveDoErro(codigo: string, status: number): Chave | null {
  const chave = `erro.${codigo}`
  if (chave in CATALOGOS['pt-BR']) return chave as Chave
  if (status === 403) return 'erro.forbidden'
  if (status >= 500) return 'erro.servidor'
  return null
}
