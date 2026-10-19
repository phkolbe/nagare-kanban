/**
 * O produto em que o Nagare sozinho trabalha (decisao de 18/10/2026): `nagare` por padrao e,
 * para quem pode, os produtos da Pampufa (`GET /integration/products`). O master opera neles;
 * o administrator e o viewer so leem (papel `viewer` ali). Fica guardado no navegador
 * (`nagare.produto`); sair volta ao `nagare`.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import { PRODUTO_SOZINHO } from '../app/contexto'

export const CHAVE_PRODUTO = 'nagare.produto'

export interface ProdutoDisponivel {
  id: string
  role: string
  project: { id: string; key: string; name: string } | null
  board: { id: string; name: string } | null
}

function produtoGuardado(): string {
  try {
    return window.localStorage.getItem(CHAVE_PRODUTO) || PRODUTO_SOZINHO
  } catch {
    return PRODUTO_SOZINHO
  }
}

function guardarProduto(produto: string): void {
  try {
    if (produto === PRODUTO_SOZINHO) window.localStorage.removeItem(CHAVE_PRODUTO)
    else window.localStorage.setItem(CHAVE_PRODUTO, produto)
  } catch {
    /* sem armazenamento: vale nesta pagina */
  }
}

interface ContextoProduto {
  produto: string
  trocar: (produto: string) => void
}

const Contexto = createContext<ContextoProduto | null>(null)

export function ProvedorProdutoSozinho({ children }: { children: ReactNode }) {
  const [produto, setProduto] = useState(produtoGuardado)
  const trocar = useCallback((novo: string) => {
    guardarProduto(novo)
    setProduto(novo)
  }, [])
  const valor = useMemo(() => ({ produto, trocar }), [produto, trocar])
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>
}

export function useProdutoSozinho(): ContextoProduto {
  const contexto = useContext(Contexto)
  if (!contexto) throw new Error('useProdutoSozinho fora do ProvedorProdutoSozinho')
  return contexto
}
