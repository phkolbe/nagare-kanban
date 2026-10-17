/** Consultas das metricas de um quadro: cada painel e uma chamada (um erro nao derruba os outros). */

import { useQuery } from '@tanstack/react-query'

import { useNagare } from '../../app/contexto'
import { consulta, nagare } from '../../api/nagare'
import type { ClasseServico, SegmentoVisao } from '../../api/tipos'

export type Painel =
  | 'summary'
  | 'throughput'
  | 'distribution'
  | 'cfd'
  | 'scatter'
  | 'aging'
  | 'flow-efficiency'
  | 'blockers'
  | 'demand'
  | 'littles-law'
  | 'kmm'

/** Paineis atras de `metrics_advanced` (o servidor recusa sem a flag; a tela mostra bloqueado). */
export const AVANCADOS: ReadonlySet<Painel> = new Set(['cfd', 'scatter', 'aging', 'flow-efficiency', 'blockers', 'demand', 'littles-law'])

export function flagDoPainel(painel: Painel): string {
  if (painel === 'kmm') return 'kmm_dashboard'
  return AVANCADOS.has(painel) ? 'metrics_advanced' : 'metrics_basic'
}

export type Periodo = '30d' | '90d' | '182d' | '365d' | 'all'

export const PERIODOS: { chave: Periodo; dias: number | null }[] = [
  { chave: '30d', dias: 30 },
  { chave: '90d', dias: 90 },
  { chave: '182d', dias: 182 },
  { chave: '365d', dias: 365 },
  { chave: 'all', dias: null },
]

export interface FiltrosMetricas {
  periodo: Periodo
  projeto: string
  classe: ClasseServico | ''
  segmento: SegmentoVisao
}

/** `from`/`to` do periodo: ate hoje; "tudo" comeca na criacao do quadro. */
export function janela(periodo: Periodo, hoje: string, criacao: string | null): { from: string; to: string } {
  const dias = PERIODOS.find((p) => p.chave === periodo)?.dias ?? null
  if (dias === null) return { from: (criacao ?? '2000-01-01').slice(0, 10), to: hoje }
  const inicio = new Date(`${hoje}T00:00:00Z`)
  inicio.setUTCDate(inicio.getUTCDate() - dias)
  return { from: inicio.toISOString().slice(0, 10), to: hoje }
}

export function useMetrica<T>(quadroId: string, painel: Painel, filtros: FiltrosMetricas, hoje: string, criacao: string | null, ligado: boolean) {
  const { produto } = useNagare()
  const { from, to } = janela(filtros.periodo, hoje, criacao)
  const busca = consulta({
    from,
    to,
    segment: filtros.segmento,
    project_id: filtros.projeto || undefined,
    service_class: filtros.classe || undefined,
  })
  return useQuery({
    queryKey: ['nagare', produto, 'metricas', quadroId, painel, busca],
    queryFn: () => nagare<T>(produto, `/boards/${encodeURIComponent(quadroId)}/metrics/${painel}${busca}`),
    enabled: ligado,
    staleTime: 60_000,
    placeholderData: (anterior) => anterior,
  })
}

// ----------------------------------------------------------------- formatos das respostas (schemas.py)

export interface ValorMetrica {
  value: number | null
  unit: string
  trend_pct: number | null
  sample_size: number
  previous_value: number | null
  low_confidence: boolean
  label: string | null
  description: string | null
  higher_is_better: boolean | null
}

export interface Resumo {
  metrics: Record<string, ValorMetrica>
  percentiles: Record<string, number>
  sample_size: number
  low_confidence: boolean
}

export interface Ponto {
  x: string | number
  y: number
  [campo: string]: unknown
}

export interface Serie {
  key: string
  label: string | null
  points: Ponto[]
  mark?: string
  [campo: string]: unknown
}

export interface Series {
  series: Serie[]
  categories: unknown[]
  percentiles: Record<string, number>
  x_label: string | null
  y_label: string | null
  unit: string | null
  sample_size: number
  low_confidence: boolean
  meta: Record<string, unknown>
}

export interface Kmm {
  levels: {
    level: string
    name: string
    focus: string
    readiness_pct: number
    metrics: { key: string; label: string; available: boolean; value: unknown; why: string | null }[]
  }[]
  current_level: string | null
  current_level_name?: string | null
  next_level: string | null
  next_actions: string[]
  overall_readiness_pct: number
}
