/**
 * Parte pura da area de Reports (24/11/2026): o formato do Documento que a API devolve, os
 * valores por unidade e a navegacao entre periodos. Conferida por `checks/telas.check.ts`.
 *
 * O Documento e um so para a tela, a planilha e (onda 2) o emitido: a tela so desenha.
 */

export type Unidade =
  | 'days'
  | 'percent'
  | 'count'
  | 'cards/week'
  | 'hours'
  | 'ratio'
  | 'date'
  | 'datetime'
  | 'text'
  | 'card'

export interface Indicador {
  key: string
  label: string
  value: number | null
  unit: Unidade
  previous: number | null
  trend_pct: number | null
  higher_is_better: boolean | null
  low_confidence: boolean
  hint: string | null
}

export interface CartaoNaCelula {
  key: string | null
  title: string | null
}

export type Bloco =
  | { type: 'kpis'; items: Indicador[] }
  | {
      type: 'table'
      title: string | null
      columns: { key: string; label: string; unit: Unidade }[]
      rows: Record<string, unknown>[]
      empty: string
    }
  | { type: 'bars'; title: string; unit: Unidade; items: { label: string; value: number; note?: number | string }[]; empty: string }
  | { type: 'note'; tone: 'info' | 'warning'; text: string }

export interface Secao {
  id: string
  title: string
  description: string | null
  blocks: Bloco[]
}

export type TipoDePeriodo = 'week' | 'month' | 'custom'

export interface Documento {
  template: string
  code: string
  title: string
  subtitle: string | null
  format_version: number
  product_id: string | null
  params: Record<string, unknown>
  period: { kind: TipoDePeriodo; from: string; to: string; label: string; timezone: string }
  previous: { from: string; to: string; label: string }
  generated_at: string
  scope: Record<string, unknown>
  sections: Secao[]
}

export interface ModeloDeRelatorio {
  id: string
  code: string
  title: string
  description: string
  /** o que o relatorio pede: um quadro, um projeto, projetos (opcional) ou nada */
  scope: 'board' | 'project' | 'projects' | 'none'
  flag: string
  available: boolean
}

/** O que a pessoa escolheu na tela; vira a busca da URL e a consulta da API. */
export interface Parametros {
  period: TipoDePeriodo
  /** um dia dentro da semana ou do mes; vazio: o ultimo periodo completo */
  date?: string
  from?: string
  to?: string
  board_id?: string
  project_id?: string
  project_ids?: string
}

const CHAVES: (keyof Parametros)[] = ['period', 'date', 'from', 'to', 'board_id', 'project_id', 'project_ids']

/** Os parametros a partir da busca da URL (`?period=month&board_id=...`). */
export function parametrosDaBusca(busca: URLSearchParams): Parametros {
  const periodo = busca.get('period')
  const p: Parametros = { period: periodo === 'month' || periodo === 'custom' ? periodo : 'week' }
  for (const chave of CHAVES) {
    if (chave === 'period') continue
    const valor = busca.get(chave)
    if (valor) p[chave] = valor
  }
  return p
}

/** Os parametros como busca da URL (sem os vazios), na ordem fixa. */
export function buscaDosParametros(p: Parametros): string {
  const pares = CHAVES.map((k) => [k, p[k]] as const).filter((par): par is readonly [keyof Parametros, string] => Boolean(par[1]))
  return pares.length ? `?${new URLSearchParams(pares.map(([k, v]) => [k, v])).toString()}` : ''
}

/** O relatorio ja tem o que precisa para ser pedido (o quadro ou o projeto)? */
export function prontoParaPedir(modelo: Pick<ModeloDeRelatorio, 'scope'>, p: Parametros): boolean {
  if (modelo.scope === 'board') return Boolean(p.board_id)
  if (modelo.scope === 'project') return Boolean(p.project_id)
  if (p.period === 'custom') return Boolean(p.from && p.to)
  return true
}

function somarDias(dia: string, dias: number): string {
  const d = new Date(`${dia}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + dias)
  return d.toISOString().slice(0, 10)
}

function somarMeses(dia: string, meses: number): string {
  const d = new Date(`${dia.slice(0, 7)}-01T00:00:00Z`)
  d.setUTCMonth(d.getUTCMonth() + meses)
  return d.toISOString().slice(0, 10)
}

/**
 * O periodo vizinho (‹ anterior, proximo ›) do que o Documento mostrou: semana ±7 dias, mes ±1,
 * intervalo livre do mesmo tamanho. Usa as datas do Documento, nao as da busca, para funcionar
 * tambem quando a busca nao tinha data (o ultimo periodo completo).
 */
export function periodoVizinho(doc: Pick<Documento, 'period'>, sentido: -1 | 1): Pick<Parametros, 'date' | 'from' | 'to'> {
  const { kind, from, to } = doc.period
  if (kind === 'week') return { date: somarDias(from, 7 * sentido) }
  if (kind === 'month') return { date: somarMeses(from, sentido) }
  const dias = Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1
  return { from: somarDias(from, dias * sentido), to: somarDias(to, dias * sentido) }
}

/** O periodo seguinte comeca depois de hoje? (nao ha o que mostrar adiante) */
export function proximoNoFuturo(doc: Pick<Documento, 'period'>, hoje: string): boolean {
  return doc.period.to >= hoje
}

/** Como ler a variacao: `bom`, `ruim` ou `neutro` (quando "melhor" nao se aplica ou nao mudou). */
export function tomDaVariacao(item: Pick<Indicador, 'trend_pct' | 'higher_is_better'>): 'bom' | 'ruim' | 'neutro' {
  if (item.trend_pct === null || item.trend_pct === 0 || item.higher_is_better === null) return 'neutro'
  const subiu = item.trend_pct > 0
  return subiu === item.higher_is_better ? 'bom' : 'ruim'
}

export interface Formatos {
  numero: (valor: number, casas?: number) => string
  data: (iso: string | null | undefined) => string
  dataHora: (iso: string | null | undefined) => string
  /** sufixos curtos das unidades, na lingua da pessoa */
  dias: string
  horas: string
  porSemana: string
}

/** Um valor pela unidade: `12,4 d`, `85%`, `4,5/sem`, `3,25 h`, data, data e hora. */
export function formatarValor(valor: unknown, unidade: Unidade, f: Formatos): string {
  if (valor === null || valor === undefined || valor === '') return '—'
  if (unidade === 'card') {
    const c = valor as CartaoNaCelula
    return [c.key, c.title].filter(Boolean).join(' — ') || '—'
  }
  if (unidade === 'date') return f.data(String(valor))
  if (unidade === 'datetime') return f.dataHora(String(valor))
  if (typeof valor !== 'number') return String(valor)
  switch (unidade) {
    case 'days':
      return `${f.numero(valor, valor < 10 ? 1 : 0)} ${f.dias}`
    case 'percent':
      return `${f.numero(valor, Math.abs(valor) < 10 && !Number.isInteger(valor) ? 1 : 0)}%`
    case 'cards/week':
      return `${f.numero(valor, 1)}${f.porSemana}`
    case 'hours':
      return `${f.numero(valor, Number.isInteger(valor) ? 0 : 1)} ${f.horas}`
    case 'ratio':
      return f.numero(valor, 2)
    case 'count':
      return f.numero(valor, Number.isInteger(valor) ? 0 : 1)
    default:
      return f.numero(valor, Number.isInteger(valor) ? 0 : 2)
  }
}

/** A variacao em texto: `+12%`, `−3,5%`, ou vazio quando nao ha comparacao. */
export function textoDaVariacao(trend: number | null, numero: Formatos['numero']): string {
  if (trend === null) return ''
  const sinal = trend > 0 ? '+' : trend < 0 ? '−' : ''
  return `${sinal}${numero(Math.abs(trend), Math.abs(trend) < 10 ? 1 : 0)}%`
}
