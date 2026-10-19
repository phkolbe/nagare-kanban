/**
 * Dica nas siglas (decisao de 18/10/2026): passar o mouse sobre uma sigla em qualquer tela
 * (WIP, SLE, CFD, KMM, P85, ML3, API, MCP... e as chaves dos projetos do produto, como NGR ou
 * SRADV) mostra o que ela quer dizer. Um ouvinte so na pagina acha a palavra sob o ponteiro;
 * nao mexe no DOM das telas, entao vale tambem para os textos que vem da API, do @pampufa/ui e
 * os rotulos dos graficos (SVG).
 */

import { useEffect, useRef, useState } from 'react'

import { useT, type Chave } from '../i18n'
import { useProjetos } from '../features/quadro/consultas'

/** Siglas fixas: a dica sai do catalogo (`sigla.<SIGLA>`), na lingua da pessoa. */
const FIXAS = ['WIP', 'SLE', 'CFD', 'KMM', 'API', 'MCP', 'REST', 'UI'] as const
const PERCENTIL = /^[Pp](\d{2})$/
const NIVEL = /^ML([0-6])$/
const LETRA = /[\p{L}\p{N}_]/u

interface Dica {
  texto: string
  x: number
  /** Topo e base da palavra: a dica vai embaixo, ou em cima quando nao cabe. */
  topo: number
  base: number
}

/** A palavra (letras, digitos) do texto em volta da posicao, e onde ela comeca. */
function palavraEm(texto: string, posicao: number): { palavra: string; inicio: number } | null {
  let inicio = posicao
  let fim = posicao
  while (inicio > 0 && LETRA.test(texto[inicio - 1]!)) inicio -= 1
  while (fim < texto.length && LETRA.test(texto[fim]!)) fim += 1
  return fim > inicio ? { palavra: texto.slice(inicio, fim), inicio } : null
}

export function DicaDeSiglas() {
  const { t } = useT()
  const projetos = useProjetos()
  const [dica, setDica] = useState<Dica | null>(null)
  const significado = useRef<(sigla: string) => string | null>(() => null)

  const doProjeto = new Map((projetos.data?.items ?? []).map((p) => [p.key, p.name]))
  significado.current = (sigla: string) => {
    if ((FIXAS as readonly string[]).includes(sigla)) return t(`sigla.${sigla}` as Chave)
    const percentil = PERCENTIL.exec(sigla)
    if (percentil) return t('sigla.percentil', { n: percentil[1]! })
    const nivel = NIVEL.exec(sigla)
    if (nivel) return t('sigla.nivel', { n: nivel[1]! })
    const projeto = doProjeto.get(sigla)
    return projeto ? t('sigla.projeto', { nome: projeto }) : null
  }

  useEffect(() => {
    let quadro = 0
    let ultimo: MouseEvent | null = null
    const achar = () => {
      quadro = 0
      const e = ultimo
      if (!e) return
      const alvo = e.target as Element | null
      if (!alvo || alvo.closest('input, textarea, select, [contenteditable="true"]')) return setDica(null)
      // Rotulos de grafico (SVG): o texto inteiro do elemento e a sigla.
      if (alvo instanceof SVGTextElement) {
        const sigla = (alvo.textContent ?? '').trim()
        const texto = significado.current(sigla)
        if (texto) {
          const caixa = alvo.getBoundingClientRect()
          return setDica({ texto: `${sigla}: ${texto}`, x: caixa.left, topo: caixa.top, base: caixa.bottom })
        }
        return setDica(null)
      }
      // Chrome, Edge e Safari: caretRangeFromPoint; Firefox: caretPositionFromPoint.
      const doc = document as Document & {
        caretRangeFromPoint?: (x: number, y: number) => Range | null
        caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
      }
      const faixaPonto = doc.caretRangeFromPoint?.(e.clientX, e.clientY)
      const posicao = faixaPonto ? null : doc.caretPositionFromPoint?.(e.clientX, e.clientY)
      const no = faixaPonto?.startContainer ?? posicao?.offsetNode
      const deslocamento = faixaPonto?.startOffset ?? posicao?.offset ?? 0
      if (!no || no.nodeType !== Node.TEXT_NODE) return setDica(null)
      const texto = no.textContent ?? ''
      const achada = palavraEm(texto, deslocamento)
      if (!achada) return setDica(null)
      const explicacao = significado.current(achada.palavra)
      if (!explicacao) return setDica(null)
      // So quando o ponteiro esta mesmo sobre a palavra (nao no fim da linha ou ao lado).
      const faixa = document.createRange()
      faixa.setStart(no, achada.inicio)
      faixa.setEnd(no, achada.inicio + achada.palavra.length)
      const caixa = faixa.getBoundingClientRect()
      const dentro = e.clientX >= caixa.left - 1 && e.clientX <= caixa.right + 1 && e.clientY >= caixa.top - 1 && e.clientY <= caixa.bottom + 1
      setDica(dentro ? { texto: `${achada.palavra}: ${explicacao}`, x: caixa.left, topo: caixa.top, base: caixa.bottom } : null)
    }
    const mover = (e: MouseEvent) => {
      ultimo = e
      if (!quadro) quadro = window.requestAnimationFrame(achar)
    }
    const esconder = () => setDica(null)
    document.addEventListener('mousemove', mover, { passive: true })
    document.addEventListener('scroll', esconder, { capture: true, passive: true })
    document.addEventListener('mousedown', esconder)
    window.addEventListener('blur', esconder)
    return () => {
      document.removeEventListener('mousemove', mover)
      document.removeEventListener('scroll', esconder, { capture: true })
      document.removeEventListener('mousedown', esconder)
      window.removeEventListener('blur', esconder)
      if (quadro) window.cancelAnimationFrame(quadro)
    }
  }, [])

  if (!dica) return null
  const largura = 320
  const x = Math.max(8, Math.min(dica.x, window.innerWidth - largura - 8))
  const emCima = dica.base + 80 > window.innerHeight
  return (
    <div
      className="ng-dica-sigla"
      role="tooltip"
      style={{ left: x, top: emCima ? dica.topo - 6 : dica.base + 6, maxWidth: largura, transform: emCima ? 'translateY(-100%)' : undefined }}
    >
      {dica.texto}
    </div>
  )
}
