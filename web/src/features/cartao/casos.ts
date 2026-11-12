/**
 * O caso do SR Adv no cartao (PPLAT-310, 12/11/2026). Funcoes puras, chamadas por
 * `checks/telas.check.ts`.
 */

import type { Cartao, CasoDoSrAdv } from '../../api/tipos'
import type { ItemDaCaixa } from '../../componentes/listaComFiltro'

/** O que o cartao guarda do caso escolhido (o cliente vem do caso). */
export interface VinculoDoCaso {
  case_id: string | null
  case_label: string | null
  client_id: string | null
  client_name: string | null
}

export const SEM_CASO: VinculoDoCaso = { case_id: null, case_label: null, client_id: null, client_name: null }

/** "Caso 07 · Revisional · Maria Silva": o codigo e o titulo da pasta (ou o rotulo do SR Adv). */
export function rotuloDoCaso(caso: Pick<CasoDoSrAdv, 'codigo' | 'titulo' | 'rotulo'>): string {
  const nome = caso.titulo?.trim() || caso.rotulo
  // O rotulo do SR Adv ja comeca pelo codigo ("Caso 04 · Benefício…"): nao repetir.
  if (!caso.codigo || nome.toLowerCase().startsWith(caso.codigo.toLowerCase())) return nome
  return `${caso.codigo} · ${nome}`
}

export function vinculoDoCaso(caso: CasoDoSrAdv): VinculoDoCaso {
  return { case_id: caso.id, case_label: rotuloDoCaso(caso), client_id: caso.cliente_id, client_name: caso.cliente_nome }
}

/**
 * Os casos como itens da lista: o cliente e o numero do processo no detalhe (entram no filtro);
 * o caso que o cartao ja tem continua na lista, mesmo fora da busca.
 */
export function itensDeCasos(casos: readonly CasoDoSrAdv[], atual: VinculoDoCaso, encerrado: string): ItemDaCaixa[] {
  const itens: ItemDaCaixa[] = casos.map((c) => ({
    id: c.id,
    nome: rotuloDoCaso(c),
    detalhe: [c.cliente_nome, c.numero_processo, c.situacao !== 'ativo' ? encerrado : null].filter(Boolean).join(' · ') || null,
  }))
  if (atual.case_id && !itens.some((i) => i.id === atual.case_id)) {
    itens.unshift({ id: atual.case_id, nome: atual.case_label ?? atual.case_id, detalhe: atual.client_name })
  }
  return itens
}

/** O vinculo que o cartao ja tem. */
export function vinculoDoCartao(cartao: Pick<Cartao, 'case_id' | 'case_label' | 'client_id' | 'client_name'>): VinculoDoCaso {
  return {
    case_id: cartao.case_id ?? null,
    case_label: cartao.case_label ?? null,
    client_id: cartao.client_id ?? null,
    client_name: cartao.client_name ?? null,
  }
}

/** A busca do SR Adv, sem espaco sobrando (o servidor procura o texto inteiro). */
export function termoDaBusca(texto: string): string {
  return texto.replace(/\s+/g, ' ').trim().slice(0, 100)
}
