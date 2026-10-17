/**
 * Regras do quadro que a tela antecipa (o servidor confere de novo e tem a palavra final):
 * o fluxo so anda para frente, envelope lacrado nao se move e a coluna (ou o grupo) cheia
 * recusa o cartao; o `expedite` passa pela coluna cheia com aviso (SPEC §6).
 */

import type { Cartao, ClasseServico, Coluna, SituacaoWip, TipoDemanda } from '../../api/tipos'
import type { Chave } from '../../i18n/mensagens'

/** Ordem do fluxo: montante antes de jusante, depois a posicao. */
export function ordenarColunas(colunas: readonly Coluna[]): Coluna[] {
  const peso = (c: Coluna) => (c.segment === 'upstream' ? 0 : 1)
  return [...colunas].sort((a, b) => peso(a) - peso(b) || a.position - b.position)
}

export interface Destino {
  estado: 'ok' | 'aviso' | 'invalido'
  motivo?: Chave
}

export function validarDestino(
  cartao: Pick<Cartao, 'column_id' | 'envelope_state' | 'service_class'>,
  destinoId: string,
  colunas: readonly Coluna[],
  wip: SituacaoWip | undefined,
): Destino {
  if (cartao.envelope_state === 'sealed') return { estado: 'invalido', motivo: 'mover.lacrado' }
  if (destinoId === cartao.column_id) return { estado: 'invalido', motivo: 'mover.mesma_coluna' }
  const ordem = ordenarColunas(colunas).map((c) => c.id)
  const de = ordem.indexOf(cartao.column_id)
  const para = ordem.indexOf(destinoId)
  if (para < 0) return { estado: 'invalido', motivo: 'mover.coluna_desconhecida' }
  if (de >= 0 && para < de) return { estado: 'invalido', motivo: 'mover.para_tras' }
  const destino = colunas.find((c) => c.id === destinoId)
  const celula = wip?.columns[destinoId]
  const cheia = celula?.max != null && celula.count >= celula.max
  if (cheia) {
    if (cartao.service_class === 'expedite') return { estado: 'aviso', motivo: 'mover.expedite_passa' }
    return { estado: 'invalido', motivo: 'mover.coluna_cheia' }
  }
  const origem = colunas.find((c) => c.id === cartao.column_id)
  const grupo = destino?.group_id ? wip?.groups[destino.group_id] : undefined
  const mesmoGrupo = origem?.group_id != null && origem.group_id === destino?.group_id
  if (grupo?.max != null && !mesmoGrupo && grupo.count >= grupo.max) {
    return { estado: 'invalido', motivo: 'mover.grupo_cheio' }
  }
  if (celula?.max != null && celula.count + 1 >= celula.max) return { estado: 'aviso', motivo: 'mover.no_limite' }
  return { estado: 'ok' }
}

/** Para onde o cartao pode ir pelo menu de mover (teclado): so para frente. */
export function destinosAFrente(cartao: Pick<Cartao, 'column_id'>, colunas: readonly Coluna[]): Coluna[] {
  const ordem = ordenarColunas(colunas)
  const de = ordem.findIndex((c) => c.id === cartao.column_id)
  return ordem.slice(de + 1)
}

/** Trechos contiguos de colunas do mesmo grupo (ou sem grupo), para as faixas de cima. */
export function faixasDeGrupo(colunas: readonly Coluna[]): { grupoId: string | null; inicio: number; tamanho: number }[] {
  const faixas: { grupoId: string | null; inicio: number; tamanho: number }[] = []
  colunas.forEach((coluna, indice) => {
    const ultima = faixas[faixas.length - 1]
    if (ultima && ultima.grupoId === coluna.group_id) ultima.tamanho += 1
    else faixas.push({ grupoId: coluna.group_id, inicio: indice, tamanho: 1 })
  })
  return faixas
}

// ------------------------------------------------------------------------- filtros

export interface Filtros {
  projeto: string
  responsavel: string
  etiqueta: string
  tipo: TipoDemanda | ''
  bloqueado: boolean
  texto: string
  classes: ClasseServico[]
}

export const FILTROS_VAZIOS: Filtros = {
  projeto: '',
  responsavel: '',
  etiqueta: '',
  tipo: '',
  bloqueado: false,
  texto: '',
  classes: [],
}

/** Os filtros moram na URL (link compartilhavel, voltar do navegador). */
export function lerFiltros(busca: URLSearchParams): Filtros {
  const classes = (busca.get('classe') ?? '').split(',').filter(Boolean) as ClasseServico[]
  return {
    projeto: busca.get('projeto') ?? '',
    responsavel: busca.get('responsavel') ?? '',
    etiqueta: busca.get('etiqueta') ?? '',
    tipo: (busca.get('tipo') as TipoDemanda | null) ?? '',
    bloqueado: busca.get('bloqueado') === '1',
    texto: busca.get('q') ?? '',
    classes,
  }
}

export function gravarFiltros(busca: URLSearchParams, filtros: Filtros): URLSearchParams {
  const nova = new URLSearchParams(busca)
  const valores: Record<string, string> = {
    projeto: filtros.projeto,
    responsavel: filtros.responsavel,
    etiqueta: filtros.etiqueta,
    tipo: filtros.tipo,
    bloqueado: filtros.bloqueado ? '1' : '',
    q: filtros.texto,
    classe: filtros.classes.join(','),
  }
  for (const [chave, valor] of Object.entries(valores)) {
    if (valor) nova.set(chave, valor)
    else nova.delete(chave)
  }
  return nova
}

export function contarFiltros(filtros: Filtros): number {
  return [
    filtros.projeto,
    filtros.responsavel,
    filtros.etiqueta,
    filtros.tipo,
    filtros.bloqueado,
    filtros.texto,
    filtros.classes.length > 0,
  ].filter(Boolean).length
}

/** Parametros que o servidor filtra (`GET /boards/{id}/view`); a classe filtra na tela. */
export function parametrosDaVisao(filtros: Filtros): Record<string, string | boolean | undefined> {
  return {
    project_id: filtros.projeto || undefined,
    assignee_id: filtros.responsavel || undefined,
    tag: filtros.etiqueta || undefined,
    demand_type: filtros.tipo || undefined,
    is_blocked: filtros.bloqueado || undefined,
    q: filtros.texto || undefined,
  }
}

export function filtrarNaTela(cartoes: readonly Cartao[], filtros: Filtros): Cartao[] {
  if (filtros.classes.length === 0) return [...cartoes]
  return cartoes.filter((c) => filtros.classes.includes(c.service_class))
}

// ------------------------------------------------------------------- sinais do cartao

/** Idade do cartao contra o SLE do quadro: atencao a 50%, risco a 85% (como a tela antiga). */
export function envelhecimento(idadeDias: number | null, sleDias: number | null): 'ok' | 'atencao' | 'risco' | null {
  if (idadeDias == null || !sleDias) return null
  const fracao = idadeDias / sleDias
  if (fracao >= 0.85) return 'risco'
  if (fracao >= 0.5) return 'atencao'
  return 'ok'
}

/** Prazo do cartao: vencido, vence em ate 3 dias, ou em dia. */
export function situacaoDoPrazo(prazo: string | null, hoje: string): 'vencido' | 'proximo' | 'em_dia' | null {
  if (!prazo) return null
  const dia = prazo.slice(0, 10)
  if (dia < hoje) return 'vencido'
  const limite = new Date(`${hoje}T00:00:00Z`)
  limite.setUTCDate(limite.getUTCDate() + 3)
  return dia <= limite.toISOString().slice(0, 10) ? 'proximo' : 'em_dia'
}

/** O vinculo do cartao com uma providencia da Pampufa (`custom_fields.pampufa`, NG8). */
export function providenciaDo(
  cartao: Pick<Cartao, 'custom_fields' | 'tags'>,
): { url: string | null; servico: string | null; tipo: string | null; resolvidaNaOrigem: boolean } | null {
  const vinculo = cartao.custom_fields?.pampufa as Record<string, unknown> | undefined
  if (!vinculo || typeof vinculo !== 'object') return null
  const texto = (valor: unknown) => (typeof valor === 'string' && valor ? valor : null)
  return {
    url: texto(vinculo.url),
    servico: texto(vinculo.servico),
    tipo: texto(vinculo.tipo),
    resolvidaNaOrigem: cartao.tags.includes('resolvido-na-origem'),
  }
}
