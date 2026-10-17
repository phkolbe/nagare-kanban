/** Linha do tempo do cartao: movimentos e eventos (flag `card_history`). */

import { useQuery } from '@tanstack/react-query'
import { Carregando, ErroDeApi } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import type { CartaoDetalhe, Coluna, Historico } from '../../api/tipos'
import { eficienciaDoCartao } from './eficiencia'
import { useT, type Chave } from '../../i18n'

const EVENTOS = new Set([
  'created',
  'moved',
  'updated',
  'blocked',
  'unblocked',
  'opened_envelope',
  'archived',
  'commented',
  'task_added',
  'task_completed',
])

export function AbaHistorico({ cartao, colunas }: { cartao: CartaoDetalhe; colunas: Coluna[] }) {
  const { t, dataHora, numero } = useT()
  const produto = useNagare().produto
  const historico = useQuery({
    queryKey: chaves.historico(produto, cartao.id),
    queryFn: () => nagare<Historico>(produto, `/cards/${cartao.id}/history`),
  })
  if (historico.isPending) return <Carregando texto={t('comum.carregando')} />
  if (historico.isError) return <ErroDeApi erro={historico.error} />

  const linhas = [
    ...historico.data.movements.map((m) => ({
      id: `m-${m.id}`,
      quando: m.moved_at,
      quem: m.moved_by_name,
      texto: m.from_column_name
        ? t(m.is_backward ? 'historico.voltou' : 'historico.moveu', { de: m.from_column_name, para: m.to_column_name ?? '' })
        : t('historico.entrou', { para: m.to_column_name ?? '' }),
      nota: m.note,
    })),
    ...historico.data.events
      .filter((e) => e.event_type !== 'moved')
      .map((e) => ({
        id: `e-${e.id}`,
        quando: e.created_at,
        quem: e.actor_name,
        texto: EVENTOS.has(e.event_type)
          ? t(`historico.evento.${e.event_type}` as Chave, { campo: e.field ?? '', valor: e.new_value ?? '' })
          : e.event_type,
        nota: null as string | null,
      })),
  ].sort((a, b) => b.quando.localeCompare(a.quando))

  if (linhas.length === 0) return <p className="mini">{t('historico.vazio')}</p>
  const ef = eficienciaDoCartao(historico.data.movements, colunas, cartao.column_id, Boolean(cartao.delivered_at), Date.now())
  const dias = (s: number) => numero(s / 86_400, 1)
  const total = ef.trabalhoS + ef.esperaS + ef.envelopeS || 1
  return (
    <>
      {ef.pct !== null && (
        <div className="ng-eficiencia-cartao">
          <div className="ng-subtitulo">{t('historico.eficiencia', { pct: numero(ef.pct, 0) })}</div>
          <div className="ng-barra-eficiencia" aria-hidden="true">
            <span className="trabalho" style={{ width: `${(ef.trabalhoS / total) * 100}%` }} />
            <span className="espera" style={{ width: `${(ef.esperaS / total) * 100}%` }} />
            <span className="envelope" style={{ width: `${(ef.envelopeS / total) * 100}%` }} />
          </div>
          <p className="mini">
            {t('historico.tempos', { trabalho: dias(ef.trabalhoS), espera: dias(ef.esperaS), envelope: dias(ef.envelopeS) })}
          </p>
        </div>
      )}
    <ol className="ng-linha-do-tempo">
      {linhas.map((linha) => (
        <li key={linha.id}>
          <time className="mini">{dataHora(linha.quando)}</time>
          <div>
            {linha.texto}
            {linha.quem && <span className="mini"> — {linha.quem}</span>}
            {linha.nota && <div className="mini">“{linha.nota}”</div>}
          </div>
        </li>
      ))}
    </ol>
    </>
  )
}
