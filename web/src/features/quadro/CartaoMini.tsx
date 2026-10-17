/** O cartao no quadro: classe, chave, prazo, titulo, projeto, tarefas, idade e responsavel. */

import { useDraggable } from '@dnd-kit/core'
import type { KeyboardEvent } from 'react'

import type { Cartao } from '../../api/tipos'
import { useT } from '../../i18n'
import { envelhecimento, providenciaDo, situacaoDoPrazo } from './regras'

/** Iniciais so com letras: "Admin (demo)" vira "AD", nao "A(". */
export function iniciaisDe(nome: string): string {
  const palavras = nome.match(/\p{L}+/gu) ?? []
  return ((palavras[0]?.[0] ?? '') + (palavras.length > 1 ? (palavras[palavras.length - 1]?.[0] ?? '') : '')).toUpperCase()
}

export function CartaoMini({
  cartao,
  sleDias,
  hoje,
  podeArrastar,
  abrir,
  menuMover,
}: {
  cartao: Cartao
  sleDias: number | null
  hoje: string
  podeArrastar: boolean
  abrir: () => void
  /** Abre o menu de mover pelo teclado (tecla `m`). */
  menuMover: () => void
}) {
  const { t, data, numero } = useT()
  const lacrado = cartao.envelope_state === 'sealed'
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: cartao.id,
    data: { cartao },
    disabled: !podeArrastar || lacrado,
  })
  const prazo = cartao.service_class === 'fixed_date' ? situacaoDoPrazo(cartao.due_date, hoje) : null
  const idade = envelhecimento(cartao.age_days, sleDias)
  const providencia = providenciaDo(cartao)

  const tecla = (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      abrir()
    } else if (e.key === 'm' && podeArrastar && !lacrado) {
      e.preventDefault()
      menuMover()
    }
  }

  return (
    <article
      ref={setNodeRef}
      className={`ng-cartao classe-${cartao.service_class}${isDragging ? ' arrastando' : ''}${lacrado ? ' lacrado' : ''}`}
      {...attributes}
      {...listeners}
      role="button"
      tabIndex={0}
      aria-label={t('cartao.rotulo', { chave: cartao.key, titulo: lacrado ? t('cartao.lacrado') : cartao.title })}
      onClick={abrir}
      onKeyDown={tecla}
    >
      <div className="ng-cartao-topo">
        <span className="ng-chave">{cartao.key}</span>
        {lacrado && (
          <span className="ng-sinal" title={t('cartao.lacrado')}>
            ✉
          </span>
        )}
        {cartao.is_blocked && (
          <span className="ng-sinal bloqueado" title={t('cartao.bloqueado_desde', { quando: data(cartao.blocked_since) })}>
            ⛔
          </span>
        )}
        {prazo && (
          <span className={`ng-prazo ${prazo}`} title={t(`prazo.${prazo}`)}>
            {data(cartao.due_date)}
          </span>
        )}
      </div>
      <div className="ng-titulo">{lacrado ? t('cartao.lacrado') : cartao.title}</div>
      {!lacrado && (
        <div className="ng-cartao-pe">
          {cartao.project_key && (
            <span className="ng-projeto" style={{ borderColor: cartao.project_color ?? undefined }}>
              {cartao.project_key}
            </span>
          )}
          {providencia && (
            <span className={`ng-providencia${providencia.resolvidaNaOrigem ? ' resolvida' : ''}`}>
              {providencia.resolvidaNaOrigem ? t('cartao.resolvida_na_origem') : t('cartao.providencia')}
            </span>
          )}
          {cartao.task_total ? (
            <span className="ng-tarefas" title={t('cartao.tarefas')}>
              ☑ {cartao.task_done ?? 0}/{cartao.task_total}
            </span>
          ) : null}
          <span className="ng-espaco" />
          {idade && cartao.age_days != null && (
            <span
              className={`ng-idade ${idade}`}
              title={t('cartao.idade', { dias: numero(cartao.age_days, 0), sle: sleDias ?? 0 })}
            />
          )}
          {cartao.assignee_name && (
            <span className="ng-avatar" title={cartao.assignee_name}>
              {iniciaisDe(cartao.assignee_name)}
            </span>
          )}
        </div>
      )}
    </article>
  )
}
