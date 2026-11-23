/** "Atualizado agora" / "Atualizado há 15 s" no topo do quadro e das metricas (23/11/2026). */

import { useT } from '../../i18n'
import { useAtualizacaoAutomatica, useSegundosDesde } from './atualizacao'

export function SeloAtualizado({ quadroId }: { quadroId: string }) {
  const { t } = useT()
  const conferidoEm = useAtualizacaoAutomatica(quadroId)
  const segundos = useSegundosDesde(conferidoEm)
  if (segundos === null) return null
  return (
    <span className="mini ng-selo-atualizado" title={t('atualizacao.dica')} aria-live="off">
      {segundos === 0 ? t('atualizacao.agora') : t('atualizacao.ha', { segundos })}
    </span>
  )
}
