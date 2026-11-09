/**
 * Cadastros do Nagare como itens da CaixaDeLista (regra geral de 09/11/2026: lista de cadastro
 * filtra ao digitar). Funcoes puras, chamadas por `checks/telas.check.ts`.
 */

import type { ItemDaCaixa } from '../../componentes/listaComFiltro'
import type { Eu, Pessoa, Projeto, Quadro, Vinculo } from '../../api/tipos'

/** Projetos nao arquivados ("CHAVE · Nome"); o escolhido continua na lista mesmo arquivado. */
export function itensDeProjetos(projetos: Projeto[], escolhido = '', excluir?: string): ItemDaCaixa[] {
  return projetos
    .filter((p) => p.id !== excluir && (!p.archived || p.id === escolhido))
    .map((p) => ({ id: p.id, nome: `${p.key} · ${p.name}` }))
}

/** Quadros que recebem cartao: nem arquivados nem so leitura. */
export function itensDeQuadros(quadros: Quadro[], detalhe?: (q: Quadro) => string | null): ItemDaCaixa[] {
  return quadros.filter((q) => !q.archived && !q.read_only).map((q) => ({ id: q.id, nome: q.name, detalhe: detalhe?.(q) ?? null }))
}

/** O trocador de quadro do cabecalho: todos os nao arquivados, o so leitura marcado no detalhe. */
export function itensDoTrocadorDeQuadro(quadros: Quadro[], soLeitura: string): ItemDaCaixa[] {
  return quadros.filter((q) => !q.archived).map((q) => ({ id: q.id, nome: q.name, detalhe: q.read_only ? soLeitura : null }))
}

/**
 * Pessoas que recebem cartao, com o e-mail no detalhe (entra no filtro). Quem ja estava no cartao
 * continua visivel, mesmo fora da lista atual.
 */
export function itensDePessoas(
  pessoas: Pick<Pessoa, 'id' | 'name' | 'email'>[],
  atual?: { id: string | null; nome: string | null },
): ItemDaCaixa[] {
  const itens: ItemDaCaixa[] = pessoas.map((p) => ({ id: p.id, nome: p.name, detalhe: p.email || null }))
  if (atual?.id && !itens.some((i) => i.id === atual.id)) itens.push({ id: atual.id, nome: atual.nome ?? atual.id })
  return itens
}

/**
 * O "+" ao lado do campo de projeto: quem gere quadros, no produto que usa projetos e cujos
 * projetos nao sao cadastrados em outro modulo (GJ-1: la o "Novo projeto" leva para fora).
 */
export function podeCriarProjeto(eu: Pick<Eu, 'capabilities' | 'flags'> | null | undefined, vinculo: Pick<Vinculo, 'projects_new_url'> | null | undefined): boolean {
  return Boolean(eu?.capabilities.manage_boards) && eu?.flags.multi_project !== false && !vinculo?.projects_new_url
}
