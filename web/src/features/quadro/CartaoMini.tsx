/**
 * O cartao no quadro: classe, chave, prazo, titulo, projeto, tarefas, idade e responsavel.
 *
 * E um link de verdade para a pagina do cartao (24/11/2026): o clique normal abre a gaveta, mas
 * Ctrl+clique (ou Cmd, Shift) e o clique do meio abrem a pagina em outra aba, e o menu do botao
 * direito do navegador oferece "Copiar endereco do link".
 */

import { useDraggable } from '@dnd-kit/core'
import type { KeyboardEvent, MouseEvent } from 'react'
import { useHref } from 'react-router-dom'

import { useNagare } from '../../app/contexto'
import type { Cartao } from '../../api/tipos'
import { useT } from '../../i18n'
import { envelhecimento, providenciaDo, situacaoDoPrazo } from './regras'

/** Iniciais so com letras: "Admin (demo)" vira "AD", nao "A(". */
export function iniciaisDe(nome: string): string {
  const palavras = nome.match(/\p{L}+/gu) ?? []
  return ((palavras[0]?.[0] ?? '') + (palavras.length > 1 ? (palavras[palavras.length - 1]?.[0] ?? '') : '')).toUpperCase()
}

/**
 * Setas entre os cartoes (paridade com a tela antiga): para cima e para baixo na mesma celula;
 * para os lados, a celula vizinha da mesma raia (o cartao na mesma altura, ou o ultimo dela).
 */
export function focarVizinho(atual: HTMLElement, tecla: string): boolean {
  const celula = atual.closest<HTMLElement>('.ng-celula')
  if (!celula) return false
  const daCelula = [...celula.querySelectorAll<HTMLElement>('.ng-cartao')]
  const posicao = daCelula.indexOf(atual)
  if (tecla === 'ArrowUp' || tecla === 'ArrowDown') {
    const alvo = daCelula[posicao + (tecla === 'ArrowDown' ? 1 : -1)]
    alvo?.focus()
    return Boolean(alvo)
  }
  const passo = tecla === 'ArrowRight' ? 1 : -1
  const grade = celula.parentElement
  let coluna = Number(celula.dataset.coluna) + passo
  while (grade && coluna >= 0) {
    const vizinha = grade.querySelector<HTMLElement>(`.ng-celula[data-coluna="${coluna}"][data-linha="${celula.dataset.linha}"]`)
    if (!vizinha) return false
    const cartoes = [...vizinha.querySelectorAll<HTMLElement>('.ng-cartao')]
    if (cartoes.length > 0) {
      ;(cartoes[Math.min(posicao, cartoes.length - 1)] ?? cartoes[0])!.focus()
      return true
    }
    coluna += passo
  }
  return false
}

export function CartaoMini({
  cartao,
  sleDias,
  hoje,
  podeArrastar,
  compacto = false,
  abrir,
  menuMover,
}: {
  cartao: Cartao
  sleDias: number | null
  hoje: string
  podeArrastar: boolean
  /** Formato compacto (18/10/2026): titulo numa linha; chave, projeto, tarefas e responsavel numa linha so. */
  compacto?: boolean
  abrir: () => void
  /** Abre o menu de mover pelo teclado (tecla `m`). */
  menuMover: () => void
}) {
  const { t, data, numero } = useT()
  const { rotaDoCartao } = useNagare()
  const endereco = useHref(rotaDoCartao(cartao.key))
  const lacrado = cartao.envelope_state === 'sealed'
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: cartao.id,
    data: { cartao },
    disabled: !podeArrastar || lacrado,
  })
  const prazo = cartao.service_class === 'fixed_date' ? situacaoDoPrazo(cartao.due_date, hoje) : null
  const idade = envelhecimento(cartao.age_days, sleDias)
  const providencia = providenciaDo(cartao)

  const tecla = (e: KeyboardEvent<HTMLElement>) => {
    if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
      if (focarVizinho(e.currentTarget, e.key)) e.preventDefault()
    } else if (e.key === 'Enter') {
      // Ctrl+Enter (ou Cmd, Shift) segue o link: a pagina do cartao em outra aba ou janela.
      if (e.ctrlKey || e.metaKey || e.shiftKey) return
      e.preventDefault()
      abrir()
    } else if (e.key === 'm' && podeArrastar && !lacrado) {
      e.preventDefault()
      menuMover()
    }
  }

  // O clique simples abre a gaveta; com modificador o navegador segue o link (outra aba). O clique
  // do meio nem chega aqui (vira `auxclick`) e o navegador ja abre o link em outra aba.
  const clicar = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    abrir()
  }

  return (
    <a
      ref={setNodeRef}
      href={endereco}
      draggable={false}
      className={`ng-cartao classe-${cartao.service_class}${isDragging ? ' arrastando' : ''}${lacrado ? ' lacrado' : ''}${compacto ? ' compacto' : ''}`}
      title={compacto && !lacrado ? cartao.title : undefined}
      {...attributes}
      {...listeners}
      // Sem o `role="button"` do dnd-kit: e um link (o leitor de tela anuncia e o menu copia).
      role={undefined}
      tabIndex={0}
      aria-label={t('cartao.rotulo', { chave: cartao.key, titulo: lacrado ? t('cartao.lacrado') : cartao.title })}
      onClick={clicar}
      onKeyDown={tecla}
    >
      {lacrado ? (
        // Envelope lacrado (18/10/2026): uma linha so, sem o conteudo; o projeto e o prazo ajudam a achar.
        <div className="ng-cartao-topo" title={t('cartao.lacrado')}>
          <span className="ng-envelope" aria-hidden="true">
            ✉
          </span>
          <span className="ng-chave">{cartao.key}</span>
          {cartao.project_key && (
            <span className="ng-projeto" style={{ borderColor: cartao.project_color ?? undefined }}>
              {cartao.project_key}
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
      ) : compacto ? (
        <div className="ng-cartao-topo">
          <span className="ng-chave">{cartao.key}</span>
          {cartao.project_key && (
            <span className="ng-projeto" style={{ borderColor: cartao.project_color ?? undefined }}>
              {cartao.project_key}
            </span>
          )}
          {cartao.is_blocked && (
            <span className="ng-sinal bloqueado" title={t('cartao.bloqueado_desde', { quando: data(cartao.blocked_since) })}>
              ⛔
            </span>
          )}
          {cartao.task_total ? (
            <span className="ng-tarefas" title={t('cartao.tarefas')}>
              ☑ {cartao.task_done ?? 0}/{cartao.task_total}
            </span>
          ) : null}
          <span className="ng-espaco" />
          {prazo && (
            <span className={`ng-prazo ${prazo}`} title={t(`prazo.${prazo}`)}>
              {data(cartao.due_date)}
            </span>
          )}
          {idade && cartao.age_days != null && (
            <span className={`ng-idade ${idade}`} title={t('cartao.idade', { dias: numero(cartao.age_days, 0), sle: sleDias ?? 0 })} />
          )}
          {cartao.assignee_name && (
            <span className="ng-avatar" title={cartao.assignee_name}>
              {iniciaisDe(cartao.assignee_name)}
            </span>
          )}
        </div>
      ) : (
        <div className="ng-cartao-topo">
          <span className="ng-chave">{cartao.key}</span>
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
      )}
      {!lacrado && <div className="ng-titulo">{cartao.title}</div>}
      {!lacrado && !compacto && (
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
    </a>
  )
}
