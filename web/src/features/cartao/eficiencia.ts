/**
 * Eficiencia de fluxo de um cartao (aba Historico, paridade com a tela antiga): quanto do tempo
 * ele passou em coluna de atividade (trabalho), de fila (espera) e no envelope. Puro.
 */

import type { Coluna, Movimento } from '../../api/tipos'

export interface Eficiencia {
  trabalhoS: number
  esperaS: number
  envelopeS: number
  /** Trabalho sobre o total (0 a 100), ou `null` sem tempo medido. */
  pct: number | null
}

export function eficienciaDoCartao(
  movimentos: readonly Movimento[],
  colunas: readonly Coluna[],
  colunaAtual: string,
  entregue: boolean,
  agora: number,
): Eficiencia {
  const tipo = new Map(colunas.map((c) => [c.id, c.kind]))
  const soma = { activity: 0, queue: 0, envelope: 0, done: 0 }
  for (const m of movimentos) {
    if (!m.from_column_id || m.seconds_in_from_column == null) continue
    const k = tipo.get(m.from_column_id)
    if (k) soma[k] += m.seconds_in_from_column
  }
  const ultimo = [...movimentos].sort((a, b) => a.moved_at.localeCompare(b.moved_at)).pop()
  const k = tipo.get(colunaAtual)
  if (ultimo && k && !entregue && k !== 'done') soma[k] += Math.max(0, (agora - Date.parse(ultimo.moved_at)) / 1000)
  const total = soma.activity + soma.queue + soma.envelope
  return {
    trabalhoS: soma.activity,
    esperaS: soma.queue,
    envelopeS: soma.envelope,
    pct: total > 0 ? (soma.activity / total) * 100 : null,
  }
}
