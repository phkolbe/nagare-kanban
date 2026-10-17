/** Formatos do simulador (`routers/simulator.py`, `flow/forecast.py`) usados nas telas. */

import type { ClasseServico } from '../../api/tipos'

export type TipoRun = 'monte_carlo_when' | 'monte_carlo_how_many' | 'wip_experiment' | 'class_of_service_mix' | 'replay'

export interface Run {
  id: string
  board_id: string
  name: string | null
  kind: TipoRun
  params: Record<string, unknown>
  results: Record<string, unknown>
  created_at: string
  created_by_name: string | null
}

export interface NivelMonteCarlo {
  confidence: number
  weeks?: number
  items?: number
  date: string
  date_label: string
  sentence: string
}

export interface ResultadoMonteCarlo {
  kind: 'monte_carlo_when' | 'monte_carlo_how_many'
  question: string
  sentence: string
  verdict: string
  unit: 'weeks' | 'items'
  trials: number
  samples: number[]
  levels: NivelMonteCarlo[]
  histogram: { x: number; x0?: number; x1?: number; count: number; label: string }[]
  percentiles: Record<string, number>
  low_confidence: boolean
  backlog_size?: number
  days?: number
}

export interface LinhaExperimento {
  name: string
  avg_wip: number
  delivered: number
  lead_time_avg: number | null
  lead_time_p85: number | null
  flow_efficiency_pct: number | null
  throughput_per_week?: number | null
  littles_law?: { throughput_per_week?: number }
  // mix de classes
  expedite_share_pct?: number
  overall_lead_time_p85?: number | null
  expedite_lead_time_p85?: number | null
  by_class?: Partial<Record<ClasseServico, { lead_time_p85: number | null; delivered: number }>>
}

export interface ResultadoExperimento {
  kind: 'wip_experiment' | 'class_of_service_mix' | 'replay'
  rows: LinhaExperimento[]
  verdict: string
}

export interface Comparacao {
  runs: Run[]
  rows: {
    key: string
    label: string
    unit: string | null
    lower_is_better: boolean | null
    baseline: number | null
    cells: { run_id: string; value: number | null; delta_pct: number | null; improved: boolean | null }[]
  }[]
  verdict: string | null
  baseline_run_id?: string
}

export function vazaoDa(linha: LinhaExperimento): number | null {
  return linha.throughput_per_week ?? linha.littles_law?.throughput_per_week ?? null
}

/**
 * A melhor configuracao (paridade com a tela antiga): entre as que mantem 95% da maior vazao,
 * a de menor lead time p85; sem vazao, a de menor p85.
 */
export function melhorLinha(linhas: LinhaExperimento[]): number {
  const usaveis = linhas.map((l, i) => ({ l, i })).filter((x) => x.l.lead_time_p85 != null)
  if (usaveis.length === 0) return -1
  const maior = Math.max(...usaveis.map((x) => vazaoDa(x.l) ?? 0))
  const saudaveis = usaveis.filter((x) => (vazaoDa(x.l) ?? 0) >= maior * 0.95)
  const grupo = saudaveis.length ? saudaveis : usaveis
  return grupo.reduce((melhor, x) => (x.l.lead_time_p85! < melhor.l.lead_time_p85! ? x : melhor), grupo[0]!).i
}

/** Rebalanceia o mix de classes para somar 100: as outras classes absorvem a diferenca. */
export function rebalancear(mix: Record<ClasseServico, number>, classe: ClasseServico, valor: number): Record<ClasseServico, number> {
  const novo = { ...mix, [classe]: Math.max(0, Math.min(100, Math.round(valor))) }
  const outras = (Object.keys(novo) as ClasseServico[]).filter((c) => c !== classe)
  const resto = 100 - novo[classe]
  const somaOutras = outras.reduce((s, c) => s + mix[c], 0)
  let distribuido = 0
  outras.forEach((c, i) => {
    const parte = i === outras.length - 1 ? resto - distribuido : somaOutras > 0 ? Math.round((mix[c] / somaOutras) * resto) : Math.round(resto / outras.length)
    novo[c] = Math.max(0, parte)
    distribuido += novo[c]
  })
  return novo
}
