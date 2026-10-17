/** Em que fase do projeto um cartao esta (tela do projeto). */

import type { Cartao } from '../../api/tipos'

export type Fase = 'lacrados' | 'descoberta' | 'andamento' | 'entregues'

export function faseDoCartao(c: Pick<Cartao, 'envelope_state' | 'committed_at' | 'delivered_at'>): Fase {
  if (c.envelope_state === 'sealed') return 'lacrados'
  if (c.delivered_at) return 'entregues'
  if (c.committed_at) return 'andamento'
  return 'descoberta'
}

export const FASES: Fase[] = ['lacrados', 'descoberta', 'andamento', 'entregues']
