/** Linha do tempo do cartao: movimentos e eventos (flag `card_history`). */

import { useQuery } from '@tanstack/react-query'
import { Carregando, ErroDeApi, useProduto } from '@pampufa/ui'

import { chaves, nagare } from '../../api/nagare'
import type { CartaoDetalhe, Historico } from '../../api/tipos'
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

export function AbaHistorico({ cartao }: { cartao: CartaoDetalhe }) {
  const { t, dataHora } = useT()
  const produto = useProduto().id
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
  return (
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
  )
}
