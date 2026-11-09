/**
 * Lista de cadastro com filtro ao digitar, no design do Nagare (regra geral de 09/11/2026: as
 * listas crescem). Faz o mesmo que o `SeletorDeLista` do @pampufa/ui, que nao serve aqui: o
 * dele tem texto fixo em portugues e fundo branco, e o Nagare tem ingles e o tema escuro.
 *
 * Digitar filtra (sem acento, palavras em qualquer ordem); setas, Enter e Esc funcionam como
 * num select (o Esc fecha a lista sem fechar a janela em volta). A lista fica em `position:
 * fixed`, para nao ser cortada pelo corpo da janela, que rola. Com `aoIncluir` (so para quem pode
 * criar o item), um botao "+" ao lado do campo abre o cadastro e recebe o que estava digitado.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'

import { useT } from '../i18n'
import { opcoesDaCaixa, proximoIndice, type ItemDaCaixa, type OpcaoDaCaixa } from './listaComFiltro'
import { Obrigatorio } from './Obrigatorio'

export type { ItemDaCaixa } from './listaComFiltro'

const ALTURA_MAXIMA = 280

export function CaixaDeLista({
  rotulo,
  itens,
  valor,
  aoMudar,
  vazio,
  obrigatorio = false,
  desabilitado = false,
  aoIncluir,
  rotuloIncluir,
  dica,
  compacto = false,
}: {
  rotulo: string
  itens: ItemDaCaixa[]
  /** O id escolhido; '' e a opcao vazia (ou nada escolhido). */
  valor: string
  aoMudar: (id: string) => void
  /** Texto da opcao vazia ("Sem projeto"); sem ele, nao ha opcao vazia na lista. */
  vazio?: string
  obrigatorio?: boolean
  desabilitado?: boolean
  /** Botao "+" ao lado (so para quem pode criar o item). Recebe o texto digitado. */
  aoIncluir?: (sugestao: string) => void
  rotuloIncluir?: string
  dica?: string
  /** Sem rotulo visivel e menor (ex.: o trocador de quadro no cabecalho). */
  compacto?: boolean
}) {
  const { t } = useT()
  const id = useId()
  const idLista = `${id}-lista`
  const entrada = useRef<HTMLInputElement>(null)
  const lista = useRef<HTMLUListElement>(null)
  const [aberto, setAberto] = useState(false)
  const [busca, setBusca] = useState('')
  const [ativo, setAtivo] = useState(0)
  const [posicao, setPosicao] = useState<CSSProperties>({})

  const escolhido = valor ? itens.find((i) => i.id === valor) : undefined
  const opcoes = opcoesDaCaixa(itens, aberto ? busca : '', Boolean(vazio))
  // Escolhido que nao veio na lista (ainda carregando): reticencias, nunca o id.
  const textoEscolhido = escolhido?.nome ?? (valor ? '…' : (vazio ?? ''))
  const incluir = rotuloIncluir ?? t('lista.incluir')

  useEffect(() => {
    entrada.current?.setCustomValidity(obrigatorio && !valor ? t('lista.escolha') : '')
  }, [obrigatorio, valor, t])

  // A lista acompanha o campo (janela que rola, tela que muda de tamanho) e abre para cima
  // quando nao cabe embaixo.
  useLayoutEffect(() => {
    if (!aberto) return
    const medir = () => {
      const r = entrada.current?.getBoundingClientRect()
      if (!r) return
      const embaixo = window.innerHeight - r.bottom - 8
      const acima = r.top - 8
      const paraCima = embaixo < Math.min(ALTURA_MAXIMA, 160) && acima > embaixo
      setPosicao({
        left: r.left,
        width: Math.max(r.width, 220),
        maxHeight: Math.min(ALTURA_MAXIMA, paraCima ? acima : embaixo),
        ...(paraCima ? { bottom: window.innerHeight - r.top + 3 } : { top: r.bottom + 3 }),
      })
    }
    medir()
    window.addEventListener('scroll', medir, true)
    window.addEventListener('resize', medir)
    return () => {
      window.removeEventListener('scroll', medir, true)
      window.removeEventListener('resize', medir)
    }
  }, [aberto])

  useEffect(() => {
    if (aberto) lista.current?.querySelector<HTMLElement>(`[data-indice="${ativo}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [ativo, aberto])

  function abrir() {
    setAberto(true)
    setBusca('')
    // Comeca no escolhido, como um select.
    const i = opcoesDaCaixa(itens, '', Boolean(vazio)).findIndex((o) => (o.tipo === 'item' ? o.item.id === valor : !valor))
    setAtivo(Math.max(i, 0))
  }

  function fechar() {
    setAberto(false)
    setBusca('')
  }

  function escolher(opcao: OpcaoDaCaixa | undefined) {
    if (!opcao) return
    const novo = opcao.tipo === 'item' ? opcao.item.id : ''
    if (novo !== valor) aoMudar(novo)
    fechar()
  }

  return (
    <div className={`campo ng-caixa-campo${compacto ? ' compacto' : ''}`}>
      <label htmlFor={id} className={compacto ? 'visualmente-oculto' : undefined}>
        <span>
          {rotulo}
          {obrigatorio && <Obrigatorio />}
        </span>
      </label>
      <div className="ng-caixa-linha">
        <div className="ng-caixa">
          <input
            ref={entrada}
            id={id}
            role="combobox"
            aria-expanded={aberto}
            aria-controls={idLista}
            aria-autocomplete="list"
            aria-activedescendant={aberto && opcoes[ativo] ? `${id}-op-${ativo}` : undefined}
            autoComplete="off"
            disabled={desabilitado}
            required={obrigatorio}
            value={aberto ? busca : textoEscolhido}
            placeholder={aberto && textoEscolhido ? textoEscolhido : t('lista.filtrar')}
            onFocus={abrir}
            onClick={() => !aberto && abrir()}
            onBlur={fechar}
            onChange={(e) => {
              setBusca(e.target.value)
              setAberto(true)
              setAtivo(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                if (!aberto) abrir()
                else setAtivo((a) => proximoIndice(a, 1, opcoes.length))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setAtivo((a) => proximoIndice(a, -1, opcoes.length))
              } else if (e.key === 'Enter') {
                if (aberto) {
                  e.preventDefault()
                  escolher(opcoes[ativo])
                }
              } else if (e.key === 'Escape' && aberto) {
                // Fecha a lista sem fechar a janela em volta.
                e.preventDefault()
                e.stopPropagation()
                fechar()
              } else if (e.key === 'Tab' && aberto && busca && opcoes[ativo]) {
                escolher(opcoes[ativo])
              }
            }}
          />
          <span className="ng-caixa-seta" aria-hidden="true">
            ▾
          </span>
          {aberto && !desabilitado && (
            <ul ref={lista} id={idLista} role="listbox" aria-label={rotulo} className="ng-caixa-lista" style={posicao}>
              {opcoes.length === 0 && (
                <li className="ng-caixa-nada" role="presentation">
                  {t('lista.nada', { busca: busca.trim() })}
                  {aoIncluir && (
                    <>
                      {' '}
                      <button
                        type="button"
                        className="link"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => {
                          const texto = busca.trim()
                          fechar()
                          aoIncluir(texto)
                        }}
                      >
                        {incluir}
                      </button>
                    </>
                  )}
                </li>
              )}
              {opcoes.map((o, i) => (
                <li
                  key={o.tipo === 'item' ? o.item.id : 'vazio'}
                  id={`${id}-op-${i}`}
                  data-indice={i}
                  role="option"
                  aria-selected={o.tipo === 'item' ? o.item.id === valor : !valor}
                  className={`ng-caixa-opcao${i === ativo ? ' ativa' : ''}${o.tipo === 'vazio' ? ' vazia' : ''}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseEnter={() => setAtivo(i)}
                  onClick={() => escolher(o)}
                >
                  {o.tipo === 'item' ? (
                    <>
                      <span>{o.item.nome}</span>
                      {o.item.detalhe && <span className="mini">{o.item.detalhe}</span>}
                    </>
                  ) : (
                    vazio
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
        {aoIncluir && (
          <button
            type="button"
            className="btn sec ng-caixa-incluir"
            aria-label={`${incluir} (${rotulo.toLowerCase()})`}
            title={incluir}
            disabled={desabilitado}
            // Sem tirar o foco do campo: o que estava digitado vai como sugestao.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              const texto = aberto ? busca.trim() : ''
              fechar()
              aoIncluir(texto)
            }}
          >
            +
          </button>
        )}
      </div>
      {dica && <small className="mini">{dica}</small>}
    </div>
  )
}
