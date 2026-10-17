/** O trilho do Nagare sozinho e quando um item fica fechado (puro). */

import type { NomeIcone } from '@pampufa/ui'

import type { Chave } from '../i18n/mensagens'

export interface ItemDoTrilho {
  caminho: string
  rotulo: Chave
  icone: NomeIcone
  flag?: string
  capacidade?: string
}

export const TRILHO: ItemDoTrilho[] = [
  { caminho: 'quadro', rotulo: 'menu.quadro', icone: 'quadro' },
  { caminho: 'projetos', rotulo: 'menu.projetos', icone: 'pasta', flag: 'multi_project' },
  { caminho: 'metricas', rotulo: 'menu.metricas', icone: 'grafico', flag: 'metrics_basic' },
  { caminho: 'simulador', rotulo: 'menu.simulador', icone: 'painel', flag: 'simulator', capacidade: 'simulator_boards' },
  { caminho: 'admin', rotulo: 'menu.admin', icone: 'engrenagem', capacidade: 'manage_users' },
]

/** Por que um item do trilho esta fechado (plano ou papel), ou `null` se esta aberto. */
export function bloqueioDoItem(
  item: Pick<ItemDoTrilho, 'flag' | 'capacidade'>,
  flags: Record<string, boolean>,
  capacidades: Record<string, boolean>,
): 'plano' | 'papel' | null {
  if (item.flag && flags[item.flag] === false) return 'plano'
  if (item.capacidade && !capacidades[item.capacidade]) return 'papel'
  return null
}
