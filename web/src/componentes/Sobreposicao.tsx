/**
 * Janela (modal) e gaveta lateral: Esc fecha, o foco entra e volta para onde estava. O clique
 * no fundo NAO fecha (regra geral da homologacao, 08/11/2026: um clique sem querer perdia o que
 * foi digitado); fecham o X, o Cancelar/Fechar de cada tela e o Esc.
 */

import { useEffect, useRef, type ReactNode } from 'react'

import { useT } from '../i18n'

/**
 * As sobreposicoes abertas, da de baixo para a de cima. Uma janela pode abrir outra por cima (o
 * "+" ao lado de uma lista abre o cadastro, 09/11/2026): o Esc fecha so a de cima.
 */
const abertas: object[] = []

function useFecharComEsc(fechar: () => void) {
  const eu = useRef({})
  useEffect(() => {
    const marca = eu.current
    abertas.push(marca)
    return () => {
      abertas.splice(abertas.indexOf(marca), 1)
    }
  }, [])
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && abertas[abertas.length - 1] === eu.current) {
        e.stopPropagation()
        fechar()
      }
    }
    window.addEventListener('keydown', tecla)
    return () => window.removeEventListener('keydown', tecla)
  }, [fechar])
}

function useFocoPreso<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  useEffect(() => {
    const anterior = document.activeElement as HTMLElement | null
    const alvo = ref.current?.querySelector<HTMLElement>('[autofocus], input, select, textarea, button')
    ;(alvo ?? ref.current)?.focus()
    return () => anterior?.focus?.()
  }, [])
  return ref
}

export function Janela({
  titulo,
  fechar,
  children,
  rodape,
  largura = 520,
}: {
  titulo: string
  fechar: () => void
  children: ReactNode
  rodape?: ReactNode
  largura?: number
}) {
  const { t } = useT()
  useFecharComEsc(fechar)
  const ref = useFocoPreso<HTMLDivElement>()
  return (
    <div className="ng-fundo">
      <div
        className="ng-janela"
        role="dialog"
        aria-modal="true"
        aria-label={titulo}
        ref={ref}
        tabIndex={-1}
        style={{ maxWidth: largura }}
      >
        <div className="ng-janela-topo">
          <h2>{titulo}</h2>
          <button type="button" className="ng-fechar" aria-label={t('comum.fechar')} onClick={fechar}>
            ×
          </button>
        </div>
        <div className="ng-janela-corpo">{children}</div>
        {rodape && <div className="ng-janela-pe">{rodape}</div>}
      </div>
    </div>
  )
}

export function Gaveta({
  titulo,
  fechar,
  children,
}: {
  titulo: string
  fechar: () => void
  children: ReactNode
}) {
  const { t } = useT()
  useFecharComEsc(fechar)
  const ref = useFocoPreso<HTMLElement>()
  return (
    <div className="ng-fundo ng-fundo-gaveta">
      <aside className="ng-gaveta" role="dialog" aria-modal="true" aria-label={titulo} ref={ref} tabIndex={-1}>
        <button type="button" className="ng-fechar ng-fechar-gaveta" aria-label={t('comum.fechar')} onClick={fechar}>
          ×
        </button>
        {children}
      </aside>
    </div>
  )
}
