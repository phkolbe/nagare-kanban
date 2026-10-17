/** As travas da administracao de pessoas (puro). */

import type { Chave } from '../../i18n/mensagens'

/** O que a pessoa pode fazer com outra conta (as travas da tela antiga). */
export function travaDoUsuario(
  eu: { id: string; role: string },
  alvo: { id: string; role: string; is_active: boolean },
  mastersAtivos: number,
): Chave | null {
  if (alvo.id === eu.id) return 'admin.trava_voce'
  if (alvo.role === 'master' && eu.role !== 'master') return 'admin.trava_master'
  if (alvo.role === 'master' && alvo.is_active && mastersAtivos <= 1) return 'admin.trava_ultimo_master'
  return null
}
