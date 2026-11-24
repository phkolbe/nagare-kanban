/**
 * O tempo do cartao frente ao SLE do quadro (decisao de 24/11/2026), no lugar da bolinha colorida:
 * o selo "⏱ 12 d" no pe do cartao e uma barra fina na base que enche ate o SLE. Verde ate 70% do
 * SLE, ambar ate o SLE, vermelho alem. A cor nao carrega o significado sozinha: o simbolo do selo
 * muda (⏱, ⏳, ⚠), a barra mostra a fracao pelo tamanho e a dica diz "12 dias de 25 do SLE (48%)".
 * Sem SLE no quadro, so o selo neutro, sem barra.
 */

import { useT } from '../../i18n'
import { tempoDoCartao, type SituacaoDoTempo, type TempoDoCartao } from './regras'

/** Texto de apresentacao (U+FE0E): o simbolo segue a cor do selo em vez de virar emoji colorido. */
const SIMBOLO: Record<SituacaoDoTempo, string> = {
  neutro: '⏱︎',
  ok: '⏱︎',
  atencao: '⏳︎',
  risco: '⚠︎',
}

/** O tempo do cartao e a frase da dica ("12 dias de 25 do SLE (48%)"), na lingua da tela. */
export function useTempoDoCartao(idadeDias: number | null, sleDias: number | null): { tempo: TempoDoCartao; dica: string } | null {
  const { t } = useT()
  const tempo = tempoDoCartao(idadeDias, sleDias)
  if (!tempo) return null
  const um = tempo.dias === 1
  const dica =
    tempo.pct == null
      ? t(um ? 'cartao.tempo_sem_sle_um' : 'cartao.tempo_sem_sle', { dias: tempo.dias })
      : t(um ? 'cartao.tempo_dica_um' : 'cartao.tempo_dica', { dias: tempo.dias, sle: sleDias ?? 0, pct: tempo.pct })
  return { tempo, dica }
}

export function SeloDoTempo({ tempo, dica }: { tempo: TempoDoCartao; dica: string }) {
  const { t } = useT()
  return (
    <span className={`ng-tempo ${tempo.situacao}`} title={dica} aria-label={dica} role="img">
      <span aria-hidden="true">{SIMBOLO[tempo.situacao]}</span> {t('cartao.tempo_selo', { dias: tempo.dias })}
    </span>
  )
}

/** A barra fina na base do cartao: enche ate o SLE; alem dele fica cheia e vermelha. */
export function BarraDoTempo({ tempo }: { tempo: TempoDoCartao }) {
  if (tempo.fracao == null) return null
  const largura = `${Math.min(1, tempo.fracao) * 100}%`
  return (
    <span className={`ng-tempo-barra ${tempo.situacao}`} aria-hidden="true">
      <span style={{ width: largura }} />
    </span>
  )
}
