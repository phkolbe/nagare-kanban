/**
 * Navegacao pelo quadro largo (decisao de 18/10/2026): a grade cabe na altura da janela (a
 * barra de rolagem horizontal fica sempre a vista e os cabecalhos ficam fixos ao rolar), um
 * minimapa das colunas com a janela visivel (clicar leva a coluna; arrastar a janela desliza),
 * botoes nas laterais que andam uma coluna por vez e arrastar o fundo vazio para os lados.
 */

import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type RefObject } from 'react'

import type { Coluna } from '../../api/tipos'
import { useT } from '../../i18n'

interface Geometria {
  colunas: { esq: number; larg: number }[]
  rolagem: number
  visivel: number
  total: number
}

/** Onde cada coluna esta na grade e que parte dela se ve (atualiza ao rolar e ao redimensionar). */
function useGeometria(alvo: RefObject<HTMLDivElement>, chave: string): Geometria | null {
  const [g, setG] = useState<Geometria | null>(null)
  // useEffect (e nao de layout): o minimapa vem antes da grade, cuja ref so existe depois.
  useEffect(() => {
    const el = alvo.current
    if (!el) return
    let quadro = 0
    const medir = () => {
      quadro = 0
      const cabecalhos = [...el.querySelectorAll<HTMLElement>('.ng-cabecalho')]
      setG({
        colunas: cabecalhos.map((c) => ({ esq: c.offsetLeft, larg: c.offsetWidth })),
        rolagem: el.scrollLeft,
        visivel: el.clientWidth,
        total: el.scrollWidth,
      })
    }
    const pedir = () => {
      if (!quadro) quadro = window.requestAnimationFrame(medir)
    }
    medir()
    const observador = new ResizeObserver(pedir)
    observador.observe(el)
    if (el.firstElementChild) observador.observe(el.firstElementChild)
    el.addEventListener('scroll', pedir, { passive: true })
    return () => {
      observador.disconnect()
      el.removeEventListener('scroll', pedir)
      if (quadro) window.cancelAnimationFrame(quadro)
    }
  }, [alvo, chave])
  return g
}

function rolarPara(el: HTMLElement, esquerda: number) {
  const suave = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollTo({ left: Math.max(0, esquerda), behavior: suave ? 'smooth' : 'auto' })
}

/**
 * A grade ocupa o resto da janela: a altura maxima e as alturas das linhas fixas (faixa e
 * grupos) viram variaveis CSS do elemento que rola.
 */
export function useGradeNaTela(alvo: RefObject<HTMLDivElement>, chave: unknown) {
  useLayoutEffect(() => {
    const el = alvo.current
    if (!el) return
    const medir = () => {
      const topo = el.getBoundingClientRect().top + window.scrollY
      el.style.setProperty('--ng-altura-grade', `${Math.max(360, window.innerHeight - topo - 16)}px`)
      const faixa = el.querySelector<HTMLElement>('.ng-faixa')
      const grupo = el.querySelector<HTMLElement>('.ng-grupo')
      const alturaFaixa = faixa?.offsetHeight ?? 0
      el.style.setProperty('--ng-topo-grupo', `${alturaFaixa}px`)
      el.style.setProperty('--ng-topo-cabecalho', `${alturaFaixa + (grupo?.offsetHeight ?? 0)}px`)
    }
    medir()
    const observador = new ResizeObserver(medir)
    const conteudo = el.closest('.content')
    if (conteudo) observador.observe(conteudo)
    window.addEventListener('resize', medir)
    return () => {
      observador.disconnect()
      window.removeEventListener('resize', medir)
    }
  }, [alvo, chave])
}

/** Arrastar o fundo vazio da grade desliza o quadro (so o mouse; o toque ja rola sozinho). */
export function usePanoramica(alvo: RefObject<HTMLDivElement>) {
  const inicio = useRef<{ x: number; y: number; esq: number; topo: number } | null>(null)
  const aoPressionar = (e: PointerEvent<HTMLDivElement>) => {
    const el = alvo.current
    if (!el || e.pointerType !== 'mouse' || e.button !== 0) return
    if ((e.target as HTMLElement).closest('.ng-cartao, button, a, input, select, textarea, label, [role="button"]')) return
    inicio.current = { x: e.clientX, y: e.clientY, esq: el.scrollLeft, topo: el.scrollTop }
    el.setPointerCapture(e.pointerId)
    el.classList.add('deslizando')
  }
  const aoMover = (e: PointerEvent<HTMLDivElement>) => {
    const el = alvo.current
    if (!el || !inicio.current) return
    el.scrollLeft = inicio.current.esq - (e.clientX - inicio.current.x)
    el.scrollTop = inicio.current.topo - (e.clientY - inicio.current.y)
  }
  const aoSoltar = (e: PointerEvent<HTMLDivElement>) => {
    const el = alvo.current
    if (!el || !inicio.current) return
    inicio.current = null
    if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId)
    el.classList.remove('deslizando')
  }
  return { onPointerDown: aoPressionar, onPointerMove: aoMover, onPointerUp: aoSoltar, onPointerCancel: aoSoltar }
}

/** Os botoes das laterais: andam ate a borda da proxima coluna (ou da anterior). */
export function SetasDaGrade({ alvo, chave }: { alvo: RefObject<HTMLDivElement>; chave: string }) {
  const { t } = useT()
  const g = useGeometria(alvo, chave)
  if (!g || g.total <= g.visivel + 1) return null
  const andar = (sentido: 1 | -1) => {
    const el = alvo.current
    if (!el) return
    const bordas = g.colunas.map((c) => c.esq)
    const destino =
      sentido === 1
        ? (bordas.find((b) => b > el.scrollLeft + 2) ?? g.total)
        : ([...bordas].reverse().find((b) => b < el.scrollLeft - 2) ?? 0)
    rolarPara(el, destino)
  }
  return (
    <>
      {g.rolagem > 1 && (
        <button type="button" className="ng-seta-grade esquerda" aria-label={t('quadro.coluna_anterior')} title={t('quadro.coluna_anterior')} onClick={() => andar(-1)}>
          ‹
        </button>
      )}
      {g.rolagem + g.visivel < g.total - 1 && (
        <button type="button" className="ng-seta-grade direita" aria-label={t('quadro.proxima_coluna')} title={t('quadro.proxima_coluna')} onClick={() => andar(1)}>
          ›
        </button>
      )}
    </>
  )
}

/**
 * O minimapa: um bloco por coluna (nome, cartoes, montante/jusante, a linha do compromisso) e a
 * janela do que se ve. Clicar num bloco leva a coluna; arrastar a janela desliza o quadro.
 */
export function MiniMapa({
  alvo,
  colunas,
  contagem,
  recolhidas,
}: {
  alvo: RefObject<HTMLDivElement>
  colunas: Coluna[]
  contagem: Map<string, number>
  recolhidas: Set<string>
}) {
  const { t } = useT()
  const g = useGeometria(alvo, colunas.map((c) => c.id).join(','))
  const ref = useRef<HTMLDivElement>(null)
  const [largura, setLargura] = useState(0)
  const arrasto = useRef<{ x: number; esq: number } | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observador = new ResizeObserver(() => setLargura(el.clientWidth))
    observador.observe(el)
    setLargura(el.clientWidth)
    return () => observador.disconnect()
  }, [])
  const pronto = g !== null && g.total > g.visivel + 1 && g.colunas.length === colunas.length
  if (!g || !pronto) return <div ref={ref} className="ng-mapa vazio" aria-hidden="true" />
  const escala = largura / g.total
  const compromisso = colunas.findIndex((c, i) => i > 0 && c.segment === 'downstream' && colunas[i - 1]!.segment === 'upstream')
  const aoPressionar = (e: PointerEvent<HTMLDivElement>) => {
    const el = alvo.current
    if (!el || e.button !== 0) return
    arrasto.current = { x: e.clientX, esq: el.scrollLeft }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const aoMover = (e: PointerEvent<HTMLDivElement>) => {
    const el = alvo.current
    if (!el || !arrasto.current || !escala) return
    el.scrollLeft = arrasto.current.esq + (e.clientX - arrasto.current.x) / escala
  }
  const aoSoltar = (e: PointerEvent<HTMLDivElement>) => {
    arrasto.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  return (
    <div ref={ref} className="ng-mapa" role="navigation" aria-label={t('quadro.minimapa')}>
      {colunas.map((c, i) => {
        const geo = g.colunas[i]!
        const n = contagem.get(c.id) ?? 0
        return (
          <button
            key={c.id}
            type="button"
            className={`ng-mapa-coluna ${c.segment}${recolhidas.has(c.id) ? ' recolhida' : ''}`}
            style={{ left: geo.esq * escala, width: Math.max(4, geo.larg * escala - 2) }}
            title={t('quadro.ir_para_coluna', { nome: c.name, n })}
            aria-label={t('quadro.ir_para_coluna', { nome: c.name, n })}
            onClick={() => alvo.current && rolarPara(alvo.current, geo.esq - 8)}
          >
            <span className="nome">{c.name}</span>
            <span className="n">{n}</span>
          </button>
        )
      })}
      {compromisso > 0 && (
        <span className="ng-mapa-compromisso" style={{ left: g.colunas[compromisso]!.esq * escala - 2 }} aria-hidden="true" />
      )}
      <div
        className="ng-mapa-janela"
        style={{ left: g.rolagem * escala, width: Math.max(12, g.visivel * escala) }}
        title={t('quadro.arrastar_janela')}
        onPointerDown={aoPressionar}
        onPointerMove={aoMover}
        onPointerUp={aoSoltar}
        onPointerCancel={aoSoltar}
      />
    </div>
  )
}
