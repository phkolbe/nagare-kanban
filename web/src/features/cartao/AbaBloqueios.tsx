/** Bloqueios do cartao: bloquear com motivo e categoria, desbloquear, e os anteriores. */

import { useState } from 'react'

import { CATEGORIAS_BLOQUEIO, type CartaoDetalhe, type CategoriaBloqueio } from '../../api/tipos'
import { useT } from '../../i18n'
import { useAcaoNoCartao } from './acoes'

function duracao(segundos: number | null, t: ReturnType<typeof useT>['t']): string {
  if (segundos == null) return ''
  const dias = segundos / 86_400
  if (dias >= 1) return t('bloqueio.dias', { n: Math.round(dias) })
  return t('bloqueio.horas', { n: Math.max(1, Math.round(segundos / 3600)) })
}

export function AbaBloqueios({ cartao, editavel }: { cartao: CartaoDetalhe; editavel: boolean }) {
  const { t, dataHora } = useT()
  const acao = useAcaoNoCartao(cartao)
  const [motivo, setMotivo] = useState('')
  const [categoria, setCategoria] = useState<CategoriaBloqueio>('other')
  const ativo = cartao.blockers.find((b) => !b.ended_at)
  const anteriores = cartao.blockers.filter((b) => b.ended_at)

  return (
    <div>
      {ativo ? (
        <div className="ng-bloqueio-ativo">
          <b>{t('bloqueio.ativo', { desde: dataHora(ativo.started_at) })}</b>
          <p>{ativo.reason}</p>
          {ativo.category && <p className="mini">{t(`bloqueio.categoria.${ativo.category}`)}</p>}
          {editavel && (
            <button
              type="button"
              className="btn sm"
              disabled={acao.isPending}
              onClick={() => acao.mutate({ caminho: `/cards/${cartao.id}/unblock`, metodo: 'POST' })}
            >
              {t('bloqueio.desbloquear')}
            </button>
          )}
        </div>
      ) : (
        editavel && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (!motivo.trim()) return
              acao.mutate(
                { caminho: `/cards/${cartao.id}/block`, metodo: 'POST', corpo: { reason: motivo.trim(), category: categoria } },
                { onSuccess: () => setMotivo('') },
              )
            }}
          >
            <label className="campo">
              <span>{t('bloqueio.motivo')}</span>
              <input value={motivo} maxLength={2000} onChange={(e) => setMotivo(e.target.value)} />
            </label>
            <label className="campo">
              <span>{t('bloqueio.categoria')}</span>
              <select value={categoria} onChange={(e) => setCategoria(e.target.value as CategoriaBloqueio)}>
                {CATEGORIAS_BLOQUEIO.map((c) => (
                  <option key={c} value={c}>
                    {t(`bloqueio.categoria.${c}`)}
                  </option>
                ))}
              </select>
            </label>
            <button className="btn sm" type="submit" disabled={!motivo.trim() || acao.isPending}>
              {t('bloqueio.bloquear')}
            </button>
          </form>
        )
      )}
      <h3 className="ng-subtitulo">{t('bloqueio.anteriores')}</h3>
      {anteriores.length === 0 ? (
        <p className="mini">{t('bloqueio.nenhum')}</p>
      ) : (
        <ul className="ng-linha-do-tempo">
          {anteriores.map((b) => (
            <li key={b.id}>
              <time className="mini">{dataHora(b.started_at)}</time>
              <div>
                {b.reason}
                <span className="mini">
                  {' '}
                  — {b.category ? t(`bloqueio.categoria.${b.category}`) : ''} {duracao(b.duration_seconds, t)}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
