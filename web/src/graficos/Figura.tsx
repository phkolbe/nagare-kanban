/**
 * Moldura comum dos graficos (paridade com `charts.js`): largura responsiva, titulo e nota de
 * leitura, legenda, dica ao passar o mouse, navegacao pelos valores com as setas (o valor vai
 * para a dica e e anunciado), e a tabela de dados ao lado (para quem nao enxerga o grafico).
 */

import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import { useT } from '../i18n'

export interface ItemLegenda {
  chave: string
  rotulo: string
  cor: string
  forma?: Forma
}

export type Forma = 'circulo' | 'triangulo' | 'quadrado' | 'losango'

export interface Desenho {
  largura: number
  /** Valor em foco (mouse ou teclado), ou `null`. */
  foco: number | null
  focar: (indice: number | null) => void
}

export interface Tabela {
  colunas: string[]
  linhas: (string | number)[][]
}

export function useLargura<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [largura, setLargura] = useState(600)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const medir = () => setLargura(Math.max(240, Math.floor(el.getBoundingClientRect().width)))
    medir()
    const observador = new ResizeObserver(medir)
    observador.observe(el)
    return () => observador.disconnect()
  }, [])
  return [ref, largura]
}

export function Marca({ forma = 'circulo', cor, x, y, r = 4 }: { forma?: Forma; cor: string; x: number; y: number; r?: number }) {
  if (forma === 'quadrado') return <rect x={x - r} y={y - r} width={r * 2} height={r * 2} fill={cor} />
  if (forma === 'triangulo') return <path d={`M${x},${y - r * 1.15}L${x + r * 1.1},${y + r * 0.8}L${x - r * 1.1},${y + r * 0.8}Z`} fill={cor} />
  if (forma === 'losango') return <path d={`M${x},${y - r * 1.2}L${x + r * 1.2},${y}L${x},${y + r * 1.2}L${x - r * 1.2},${y}Z`} fill={cor} />
  return <circle cx={x} cy={y} r={r} fill={cor} />
}

export function Figura({
  titulo,
  nota,
  legenda,
  itens,
  tabela,
  vazio,
  textoVazio,
  acoes,
  children,
}: {
  titulo: string
  /** Como ler / o que e saudavel (a nota de cada painel da tela antiga). */
  nota?: ReactNode
  legenda?: ItemLegenda[]
  /** Descricao de cada valor, na ordem da navegacao por teclado. */
  itens: string[]
  tabela?: Tabela
  vazio?: boolean
  textoVazio?: string
  acoes?: ReactNode
  children: (desenho: Desenho) => ReactNode
}) {
  const { t } = useT()
  const [ref, largura] = useLargura<HTMLDivElement>()
  const [foco, setFoco] = useState<number | null>(null)
  const [verTabela, setVerTabela] = useState(false)
  const idTitulo = useId()
  const idNota = useId()

  const tecla = (e: KeyboardEvent) => {
    if (itens.length === 0) return
    const atual = foco ?? -1
    const mover = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]
    if (mover !== undefined) {
      e.preventDefault()
      setFoco(Math.max(0, Math.min(itens.length - 1, atual + mover)))
    } else if (e.key === 'Home') {
      e.preventDefault()
      setFoco(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setFoco(itens.length - 1)
    } else if (e.key === 'Escape') {
      setFoco(null)
    }
  }

  return (
    <figure className="ng-figura" role="group" aria-labelledby={idTitulo} aria-describedby={nota ? idNota : undefined}>
      <figcaption className="ng-figura-topo">
        <h3 id={idTitulo}>{titulo}</h3>
        {acoes}
      </figcaption>
      {nota && (
        <p className="mini ng-figura-nota" id={idNota}>
          {nota}
        </p>
      )}
      <div ref={ref} className="ng-figura-corpo">
        {vazio ? (
          <p className="mini ng-figura-vazia">{textoVazio ?? t('grafico.sem_dados')}</p>
        ) : (
          <div
            className="ng-figura-desenho"
            tabIndex={0}
            onKeyDown={tecla}
            onBlur={() => setFoco(null)}
            aria-label={t('grafico.navegar', { titulo })}
          >
            {children({ largura, foco, focar: setFoco })}
            {foco !== null && itens[foco] && (
              <div className="ng-dica" role="status">
                {itens[foco]}
              </div>
            )}
          </div>
        )}
      </div>
      {legenda && legenda.length > 1 && (
        <ul className="ng-legenda">
          {legenda.map((l) => (
            <li key={l.chave}>
              <svg width="12" height="12" aria-hidden="true">
                <Marca forma={l.forma} cor={l.cor} x={6} y={6} r={5} />
              </svg>
              {l.rotulo}
            </li>
          ))}
        </ul>
      )}
      {tabela && !vazio && (
        <div className="ng-figura-tabela">
          <button type="button" className="link mini" aria-expanded={verTabela} onClick={() => setVerTabela(!verTabela)}>
            {verTabela ? t('grafico.ocultar_dados') : t('grafico.mostrar_dados')}
          </button>
          {verTabela && (
            <div className="tabela-rolagem">
              <table className="ng-tabela">
                <thead>
                  <tr>
                    {tabela.colunas.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tabela.linhas.map((linha, i) => (
                    <tr key={i}>
                      {linha.map((celula, j) => (
                        <td key={j}>{celula}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </figure>
  )
}
