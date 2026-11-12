/**
 * Tipos de cartao do produto (PPLAT-312, 12/11/2026): a lista vem de `/card-types`; um tipo sem
 * nome e o da lista padrao, com o rotulo das telas (`area.<chave>`). Funcoes puras, chamadas por
 * `checks/telas.check.ts`.
 */

import { AREAS_VALOR, type TipoDeCartao } from '../../api/tipos'
import { CATALOGOS, type Chave } from '../../i18n/mensagens'

/** A lista padrao do Nagare, enquanto a do produto nao chega. */
export const TIPOS_PADRAO: TipoDeCartao[] = AREAS_VALOR.map((key, position) => ({
  key,
  name: null,
  position,
  archived: false,
  builtin: true,
}))

/** O nome do tipo: o do produto; sem nome, o das telas; desconhecido, a propria chave. */
export function rotuloDoTipo(chave: string, tipos: readonly TipoDeCartao[] | undefined, t: (c: Chave) => string): string {
  const tipo = (tipos ?? TIPOS_PADRAO).find((x) => x.key === chave)
  if (tipo?.name) return tipo.name
  const daTela = `area.${chave}`
  return daTela in CATALOGOS['pt-BR'] ? t(daTela as Chave) : chave
}

/**
 * As opcoes do campo "Tipo de cartao": os tipos em uso, na ordem do produto; o que o cartao ja tem
 * continua na lista mesmo arquivado (o cartao nunca perde o tipo).
 */
export function opcoesDeTipo(
  tipos: readonly TipoDeCartao[] | undefined,
  atual: string | null,
  t: (c: Chave) => string,
): { valor: string; texto: string }[] {
  const lista = tipos ?? TIPOS_PADRAO
  const ordem = [...lista].sort((a, b) => a.position - b.position)
  const opcoes = ordem
    .filter((x) => !x.archived || x.key === atual)
    .map((x) => ({
      valor: x.key,
      texto: x.archived ? `${rotuloDoTipo(x.key, lista, t)} (${t('tipos.arquivado')})` : rotuloDoTipo(x.key, lista, t),
    }))
  if (atual && !opcoes.some((o) => o.valor === atual)) opcoes.push({ valor: atual, texto: rotuloDoTipo(atual, lista, t) })
  return opcoes
}

/** O tipo de um cartao novo: o primeiro em uso (o servidor faz o mesmo quando nao vem nenhum). */
export function tipoInicial(tipos: readonly TipoDeCartao[] | undefined): string {
  const ordem = [...(tipos ?? TIPOS_PADRAO)].sort((a, b) => a.position - b.position)
  return ordem.find((x) => !x.archived)?.key ?? AREAS_VALOR[0]!
}

/** A nova ordem ao subir (-1) ou descer (+1) um tipo; fora dos limites, a mesma. */
export function moverTipo(chaves: readonly string[], chave: string, sentido: -1 | 1): string[] {
  const nova = [...chaves]
  const de = nova.indexOf(chave)
  const para = de + sentido
  if (de < 0 || para < 0 || para >= nova.length) return nova
  ;[nova[de], nova[para]] = [nova[para]!, nova[de]!]
  return nova
}
