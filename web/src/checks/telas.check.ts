/**
 * Verificacoes das telas do Nagare (modulo Tarefas) — `npm run check` em `web/`.
 *
 * As decisoes da tela vivem em funcoes puras (regras do quadro, fases, textos) e aqui sao
 * CHAMADAS com dados no formato real da API do Nagare.
 */

import type { Cartao, Coluna, SituacaoWip } from '../api/tipos'
import { faseDoCartao } from '../features/projetos/fase'
import {
  contarFiltros,
  destinosAFrente,
  envelhecimento,
  faixasDeGrupo,
  filtrarNaTela,
  FILTROS_VAZIOS,
  gravarFiltros,
  lerFiltros,
  ordenarColunas,
  parametrosDaVisao,
  providenciaDo,
  situacaoDoPrazo,
  validarDestino,
} from '../features/quadro/regras'
import { CATALOGOS, pt, type Chave } from '../i18n/mensagens'
import { chaveDoErro, interpolar, traducao } from '../i18n/texto'

let falhas = 0
let total = 0

function confere(nome: string, obtido: unknown, esperado: unknown): void {
  total += 1
  if (JSON.stringify(obtido) !== JSON.stringify(esperado)) {
    falhas += 1
    console.error(`FALHOU  ${nome}\n  obtido:   ${JSON.stringify(obtido)}\n  esperado: ${JSON.stringify(esperado)}`)
  }
}

// ----------------------------------------------------------------- o fluxo do modelo providencias
function coluna(
  id: string,
  position: number,
  segment: Coluna['segment'],
  kind: Coluna['kind'],
  extra: Partial<Coluna> = {},
): Coluna {
  return {
    id,
    board_id: 'q1',
    group_id: null,
    name: id,
    position,
    segment,
    kind,
    wip_min: null,
    wip_max: null,
    is_commitment_point: false,
    is_delivery_point: false,
    color: null,
    description: null,
    ...extra,
  }
}
const colunas = [
  coluna('fazendo', 2, 'downstream', 'activity', { wip_max: 3, group_id: 'g1' }),
  coluna('entrada', 0, 'upstream', 'queue'),
  coluna('a-fazer', 1, 'downstream', 'queue', { is_commitment_point: true, group_id: 'g1' }),
  coluna('aguardando', 3, 'downstream', 'queue'),
  coluna('entregue', 4, 'downstream', 'done', { is_delivery_point: true }),
]
function wip(contagens: Record<string, number>, grupo = 0, maxGrupo: number | null = null): SituacaoWip {
  return {
    board_id: 'q1',
    columns: Object.fromEntries(
      colunas.map((c) => [c.id, { count: contagens[c.id] ?? 0, min: null, max: c.wip_max, state: 'ok' as const, label: null }]),
    ),
    groups: { g1: { count: grupo, min: null, max: maxGrupo, state: 'ok', label: null } },
    flow: { count: 0, min: null, max: null, state: 'ok', label: null },
  }
}
function cartao(extra: Partial<Cartao> = {}): Cartao {
  return { column_id: 'entrada', envelope_state: 'opened', service_class: 'standard', tags: [], custom_fields: {}, ...extra } as Cartao
}

confere('ordem do fluxo: montante e depois posicao', ordenarColunas(colunas).map((c) => c.id), [
  'entrada',
  'a-fazer',
  'fazendo',
  'aguardando',
  'entregue',
])
confere('para frente pode', validarDestino(cartao(), 'a-fazer', colunas, wip({})), { estado: 'ok' })
confere('para tras nao', validarDestino(cartao({ column_id: 'fazendo' }), 'entrada', colunas, wip({})).motivo, 'mover.para_tras')
confere('mesma coluna nao', validarDestino(cartao(), 'entrada', colunas, wip({})).motivo, 'mover.mesma_coluna')
confere(
  'lacrado nao se move',
  validarDestino(cartao({ envelope_state: 'sealed' }), 'a-fazer', colunas, wip({})).motivo,
  'mover.lacrado',
)
confere('coluna cheia recusa', validarDestino(cartao({ column_id: 'a-fazer' }), 'fazendo', colunas, wip({ fazendo: 3 })), {
  estado: 'invalido',
  motivo: 'mover.coluna_cheia',
})
confere(
  'urgente passa na coluna cheia, com aviso',
  validarDestino(cartao({ column_id: 'a-fazer', service_class: 'expedite' }), 'fazendo', colunas, wip({ fazendo: 3 })).estado,
  'aviso',
)
confere(
  'ultima vaga avisa',
  validarDestino(cartao({ column_id: 'a-fazer' }), 'fazendo', colunas, wip({ fazendo: 2 })).motivo,
  'mover.no_limite',
)
confere(
  'grupo cheio recusa quem vem de fora',
  validarDestino(cartao(), 'a-fazer', colunas, wip({}, 4, 4)).motivo,
  'mover.grupo_cheio',
)
confere(
  'dentro do mesmo grupo o grupo nao conta',
  validarDestino(cartao({ column_id: 'a-fazer' }), 'fazendo', colunas, wip({ fazendo: 0 }, 4, 4)).estado,
  'ok',
)
confere('menu de mover: so a frente', destinosAFrente(cartao({ column_id: 'fazendo' }), colunas).map((c) => c.id), [
  'aguardando',
  'entregue',
])
confere('faixas de grupo contiguas', faixasDeGrupo(ordenarColunas(colunas)), [
  { grupoId: null, inicio: 0, tamanho: 1 },
  { grupoId: 'g1', inicio: 1, tamanho: 2 },
  { grupoId: null, inicio: 3, tamanho: 2 },
])

// ----------------------------------------------------------------- filtros na URL
const filtros = lerFiltros(new URLSearchParams('projeto=p1&classe=expedite,fixed_date&bloqueado=1&q=extrato&cartao=c9'))
confere(
  'le os filtros',
  [filtros.projeto, filtros.classes, filtros.bloqueado, filtros.texto],
  ['p1', ['expedite', 'fixed_date'], true, 'extrato'],
)
confere('conta os filtros ativos', contarFiltros(filtros), 4)
confere(
  'grava sem apagar o resto da URL (o cartao aberto)',
  gravarFiltros(new URLSearchParams('cartao=c9&segmento=upstream'), { ...FILTROS_VAZIOS, tipo: 'failure' }).toString(),
  'cartao=c9&segmento=upstream&tipo=failure',
)
confere('servidor filtra o resto; a classe filtra na tela', parametrosDaVisao(filtros), {
  project_id: 'p1',
  is_blocked: true,
  q: 'extrato',
})
confere(
  'classe filtra na tela',
  filtrarNaTela([cartao({ service_class: 'standard' }), cartao({ service_class: 'expedite' })], filtros).map(
    (c) => c.service_class,
  ),
  ['expedite'],
)

// ----------------------------------------------------------------- sinais do cartao
confere('idade sem SLE', envelhecimento(10, null), null)
confere(
  'idade: ok, atencao, risco',
  [envelhecimento(4, 14), envelhecimento(7, 14), envelhecimento(12, 14)],
  ['ok', 'atencao', 'risco'],
)
confere(
  'prazo vencido, proximo, em dia',
  [
    situacaoDoPrazo('2026-10-16', '2026-10-17'),
    situacaoDoPrazo('2026-10-20T00:00:00Z', '2026-10-17'),
    situacaoDoPrazo('2026-11-30', '2026-10-17'),
    situacaoDoPrazo(null, '2026-10-17'),
  ],
  ['vencido', 'proximo', 'em_dia', null],
)
const daProvidencia = cartao({
  tags: ['providencia', 'resolvido-na-origem'],
  custom_fields: { pampufa: { providencia_id: 'p', servico: 'financeiro', tipo: 'extrato_atrasado', url: 'http://gw/x' } },
})
confere('cartao de providencia (NG8/NG11)', providenciaDo(daProvidencia), {
  url: 'http://gw/x',
  servico: 'financeiro',
  tipo: 'extrato_atrasado',
  resolvidaNaOrigem: true,
})
confere('cartao comum nao e providencia', providenciaDo(cartao()), null)
confere(
  'fases do projeto',
  [
    faseDoCartao({ envelope_state: 'sealed', committed_at: null, delivered_at: null }),
    faseDoCartao({ envelope_state: 'opened', committed_at: null, delivered_at: null }),
    faseDoCartao({ envelope_state: 'opened', committed_at: 'x', delivered_at: null }),
    faseDoCartao({ envelope_state: 'opened', committed_at: 'x', delivered_at: 'y' }),
  ],
  ['lacrados', 'descoberta', 'andamento', 'entregues'],
)

// ----------------------------------------------------------------- portugues e ingles (17/10/2026)
const chaves = Object.keys(pt) as Chave[]
const marcas = (texto: string) => (texto.match(/\{\w+\}/g) ?? []).sort()
confere('ingles tem toda chave do portugues', chaves.filter((c) => !(c in CATALOGOS.en)), [])
confere('nenhum texto vazio', chaves.filter((c) => !CATALOGOS.en[c].trim() || !pt[c].trim()), [])
confere(
  'mesmos marcadores nas duas linguas',
  chaves.filter((c) => JSON.stringify(marcas(pt[c])) !== JSON.stringify(marcas(CATALOGOS.en[c]))),
  [],
)
confere(
  'interpola',
  interpolar('Mover {chave} para {coluna}', { chave: 'SRADV-101', coluna: 'Fazendo' }),
  'Mover SRADV-101 para Fazendo',
)
confere('marcador sem valor fica', interpolar('{a} e {b}', { a: 1 }), '1 e {b}')
confere('texto em portugues', traducao('pt-BR').t('mover.entregue', { chave: 'SRADV-101' }), 'SRADV-101 entregue.')
confere('texto em ingles', traducao('en').t('mover.entregue', { chave: 'SRADV-101' }), 'SRADV-101 delivered.')
confere(
  'data de negocio sem fuso',
  [traducao('pt-BR').data('2026-10-17'), traducao('en').data('2026-10-17')],
  ['17/10/2026', '10/17/2026'],
)
confere('erro pelo codigo da API', chaveDoErro('flow_locked', 409), 'erro.flow_locked')
confere('erro do pampufa pelo codigo', chaveDoErro('nagare.responsavel_sem_papel', 422), 'erro.nagare.responsavel_sem_papel')
confere('403 sem traducao', chaveDoErro('algo_novo', 403), 'erro.forbidden')
confere('codigo desconhecido usa a mensagem do servidor', chaveDoErro('algo_novo', 409), null)

if (falhas > 0) throw new Error(`${falhas} de ${total} verificacoes falharam.`)
console.log(`${total} verificacoes das telas do Nagare passaram.`)
