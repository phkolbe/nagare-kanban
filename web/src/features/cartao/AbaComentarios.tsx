/** Comentarios do cartao (Ctrl/Cmd+Enter envia). Os da integracao (resolvido na origem) tambem. */

import { useState } from 'react'

import type { CartaoDetalhe } from '../../api/tipos'
import { useT } from '../../i18n'
import { useAcaoNoCartao } from './acoes'

export function AbaComentarios({ cartao, podeComentar }: { cartao: CartaoDetalhe; podeComentar: boolean }) {
  const { t, dataHora } = useT()
  const [texto, setTexto] = useState('')
  const acao = useAcaoNoCartao(cartao)
  const enviar = () => {
    if (!texto.trim()) return
    acao.mutate(
      { caminho: `/cards/${cartao.id}/comments`, metodo: 'POST', corpo: { body: texto.trim() } },
      { onSuccess: () => setTexto('') },
    )
  }
  const comentarios = [...cartao.comments].sort((a, b) => b.created_at.localeCompare(a.created_at))
  return (
    <div>
      {podeComentar && !cartao.archived && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            enviar()
          }}
        >
          <label className="campo">
            <span>{t('comentario.novo')}</span>
            <textarea
              rows={3}
              value={texto}
              maxLength={8000}
              onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  enviar()
                }
              }}
            />
            <small className="mini">{t('comentario.atalho')}</small>
          </label>
          <button className="btn sm" type="submit" disabled={!texto.trim() || acao.isPending}>
            {t('comentario.enviar')}
          </button>
        </form>
      )}
      {comentarios.length === 0 ? (
        <p className="mini">{t('comentario.nenhum')}</p>
      ) : (
        <ul className="ng-comentarios">
          {comentarios.map((c) => (
            <li key={c.id}>
              <div className="mini">
                <b>{c.author_name ?? t('comentario.sistema')}</b> · {dataHora(c.created_at)}
              </div>
              <p>{c.body}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
