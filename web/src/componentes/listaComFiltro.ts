/**
 * Logica pura da CaixaDeLista (chamada por `checks/telas.check.ts`): lista de cadastro com
 * filtro ao digitar (regra geral de 09/11/2026: as listas crescem).
 */

export interface ItemDaCaixa {
  id: string
  nome: string
  /** Texto menor ao lado do nome (ex.: a chave do projeto, o e-mail); tambem entra no filtro. */
  detalhe?: string | null
}

export type OpcaoDaCaixa = { tipo: 'vazio' } | { tipo: 'item'; item: ItemDaCaixa }

/** Para comparar sem acento, sem caixa e sem espaco sobrando. */
export function textoDeBusca(texto: string): string {
  return texto
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
}

/** Cada palavra digitada precisa aparecer no nome ou no detalhe, em qualquer ordem. */
export function filtrarDaCaixa(itens: ItemDaCaixa[], busca: string): ItemDaCaixa[] {
  const palavras = textoDeBusca(busca).split(' ').filter(Boolean)
  if (!palavras.length) return itens
  return itens.filter((i) => {
    const alvo = textoDeBusca(`${i.nome} ${i.detalhe ?? ''}`)
    return palavras.every((p) => alvo.includes(p))
  })
}

/**
 * As opcoes da lista aberta: a opcao vazia (sem projeto, sem responsavel...) primeiro, quando o
 * campo a tem e a pessoa nao esta filtrando; depois os itens que passam no filtro.
 */
export function opcoesDaCaixa(itens: ItemDaCaixa[], busca: string, temVazio: boolean): OpcaoDaCaixa[] {
  return [
    ...(temVazio && !textoDeBusca(busca) ? [{ tipo: 'vazio' } as const] : []),
    ...filtrarDaCaixa(itens, busca).map((item) => ({ tipo: 'item', item }) as const),
  ]
}

/** Setas: anda na lista sem passar das pontas. */
export function proximoIndice(atual: number, passo: number, total: number): number {
  if (total <= 0) return 0
  return Math.min(Math.max(atual + passo, 0), total - 1)
}
