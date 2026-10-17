/** Tarefas (lista de verificacao) do cartao. */

import { useState } from 'react'

import type { CartaoDetalhe } from '../../api/tipos'
import { useT } from '../../i18n'
import { useAcaoNoCartao } from './acoes'

export function AbaTarefas({ cartao, editavel }: { cartao: CartaoDetalhe; editavel: boolean }) {
  const { t } = useT()
  const [titulo, setTitulo] = useState('')
  const acao = useAcaoNoCartao(cartao)
  const tarefas = [...cartao.tasks].sort((a, b) => a.position - b.position)
  const feitas = tarefas.filter((x) => x.is_done).length
  return (
    <div>
      {tarefas.length > 0 && (
        <div className="ng-progresso" aria-label={t('tarefas.progresso', { feitas, total: tarefas.length })}>
          <div style={{ width: `${(feitas / tarefas.length) * 100}%` }} />
        </div>
      )}
      {tarefas.length === 0 && <p className="mini">{t('tarefas.nenhuma')}</p>}
      <ul className="ng-tarefas-lista">
        {tarefas.map((tarefa) => (
          <li key={tarefa.id}>
            <label className="ng-alternar">
              <input
                type="checkbox"
                checked={tarefa.is_done}
                disabled={!editavel || acao.isPending}
                onChange={(e) => acao.mutate({ caminho: `/tasks/${tarefa.id}`, metodo: 'PATCH', corpo: { is_done: e.target.checked } })}
              />
              <span className={tarefa.is_done ? 'feita' : undefined}>{tarefa.title}</span>
            </label>
            {editavel && (
              <button
                type="button"
                className="link"
                aria-label={t('tarefas.remover', { titulo: tarefa.title })}
                onClick={() => acao.mutate({ caminho: `/tasks/${tarefa.id}`, metodo: 'DELETE' })}
              >
                {t('comum.remover')}
              </button>
            )}
          </li>
        ))}
      </ul>
      {editavel && (
        <form
          className="ng-par"
          onSubmit={(e) => {
            e.preventDefault()
            if (!titulo.trim()) return
            acao.mutate({ caminho: `/cards/${cartao.id}/tasks`, metodo: 'POST', corpo: { title: titulo.trim() } })
            setTitulo('')
          }}
        >
          <input
            value={titulo}
            maxLength={500}
            placeholder={t('tarefas.nova')}
            aria-label={t('tarefas.nova')}
            onChange={(e) => setTitulo(e.target.value)}
          />
          <button className="btn sm" type="submit" disabled={!titulo.trim()}>
            {t('comum.adicionar')}
          </button>
        </form>
      )}
    </div>
  )
}
