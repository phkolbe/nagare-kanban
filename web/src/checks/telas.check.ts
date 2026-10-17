/**
 * Verificacoes das telas do Nagare (modulo Tarefas) — `npm run check` em `web/`.
 *
 * As decisoes da tela vivem em funcoes puras (regras do quadro, fases, textos) e aqui sao
 * CHAMADAS com dados no formato real da API do Nagare.
 */

import { baseDoSozinho, modoDoCaminho, montarRota } from '../app/contexto'
import { ehSoDoNagare } from '../componentes/Atalhos'
import type { Cartao, Coluna, SituacaoWip } from '../api/tipos'
import { travaDoUsuario } from '../features/admin/travas'
import { eficienciaDoCartao } from '../features/cartao/eficiencia'
import { janela } from '../features/metricas/consultas'
import { formatarIndicador, valorKmm } from '../features/metricas/formato'
import { melhorLinha, rebalancear } from '../features/simulador/tipos'
import { eixoRedondo, passoRedondo } from '../graficos/escalas'
import { rotaDoLinkAntigo } from '../sozinho/linksAntigos'
import { temaEfetivo } from '../sozinho/tema'
import { TRILHO, bloqueioDoItem } from '../sozinho/trilho'
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

// ----------------------------------------------------------------- N3: dois modos
confere('modo pelo caminho', [modoDoCaminho('/grupo-sr/sr-adv/tarefas'), modoDoCaminho('/quadro/1'), modoDoCaminho('/nagare/metricas')], [
  'plataforma',
  'sozinho',
  'sozinho',
])
confere('base do Nagare sozinho', [baseDoSozinho('/nagare/quadro'), baseDoSozinho('/quadro'), baseDoSozinho('/nagarex')], ['/nagare', '', ''])
confere('rotas nos dois modos', [montarRota('plataforma', 'sr-adv', 'quadro/1'), montarRota('sozinho', 'nagare', 'quadro/1'), montarRota('sozinho', 'nagare', '')], [
  '/sr-adv/tarefas/quadro/1',
  '/quadro/1',
  '/',
])
confere('links da tela antiga', ['#/board/abc', '#/metrics/q1', '#/admin/plans', '#/login', '#/x'].map(rotaDoLinkAntigo), [
  '/quadro/abc',
  '/metricas/q1',
  '/admin/planos',
  '/entrar',
  null,
])
confere('tema efetivo', [temaEfetivo('system', true), temaEfetivo('system', false), temaEfetivo('dark', false)], ['dark', 'light', 'dark'])
confere(
  'trilho: plano e papel',
  [
    bloqueioDoItem({ flag: 'simulator', capacidade: 'simulator_boards' }, { simulator: false }, { simulator_boards: true }),
    bloqueioDoItem({ flag: 'simulator', capacidade: 'simulator_boards' }, { simulator: true }, {}),
    bloqueioDoItem({ capacidade: 'manage_users' }, {}, { manage_users: true }),
  ],
  ['plano', 'papel', null],
)
confere('simulador e admin so no Nagare sozinho', ['g s', 'g a', 'g m', 'g b', '/', 'Enter'].map(ehSoDoNagare), [true, true, false, false, false, false])
confere('o trilho tem as cinco telas', TRILHO.map((i) => i.caminho), ['quadro', 'projetos', 'metricas', 'simulador', 'admin'])

// ----------------------------------------------------------------- N3: metricas
confere('janela de 90 dias', janela('90d', '2026-10-17', null), { from: '2026-07-19', to: '2026-10-17' })
confere('janela "tudo" desde a criacao do quadro', janela('all', '2026-10-17', '2026-06-18T10:00:00Z'), { from: '2026-06-18', to: '2026-10-17' })
const tPt = traducao('pt-BR')
confere('indicador em dias', formatarIndicador(34.75, 'days', tPt.numero), { valor: '35', unidade: 'd' })
confere('indicador curto com decimal', formatarIndicador(4.24, 'cards/week', tPt.numero), { valor: '4,2', unidade: '/sem' })
confere('indicador percentual', formatarIndicador(28.25, 'percent', tPt.numero), { valor: '28%', unidade: '' })
confere('indicador vazio', formatarIndicador(null, 'days', tPt.numero), { valor: '—', unidade: '' })
confere('KMM: percentis', valorKmm({ p50: 31.1, p85: 55.3 }, tPt.numero), 'P50 31,1 · P85 55,3')
confere('KMM: mix', valorKmm([{ key: 'standard', label: 'Padrão', pct: 71.2 }], tPt.numero), 'Padrão 71%')
confere('KMM: vazio', valorKmm(null, tPt.numero), '—')
confere('escala redonda', eixoRedondo(0, 47).marcas, [0, 10, 20, 30, 40, 50])
confere('passo redondo', [passoRedondo(7), passoRedondo(230), passoRedondo(0)], [2, 50, 1])

// ----------------------------------------------------------------- N3: simulador
const linha = (nome: string, p85: number | null, vazao: number) => ({ name: nome, avg_wip: 1, delivered: 1, lead_time_avg: 1, lead_time_p85: p85, flow_efficiency_pct: 1, throughput_per_week: vazao })
confere(
  'melhor configuracao: menor p85 entre as que mantem a vazao',
  melhorLinha([linha('livre', 22, 8.3), linha('justo', 14, 8.1), linha('apertado', 9, 0.9), linha('sem dados', null, 9)]),
  1,
)
confere('mix rebalanceado soma 100', Object.values(rebalancear({ expedite: 8, standard: 70, fixed_date: 12, intangible: 10 }, 'expedite', 30)).reduce((a, b) => a + b, 0), 100)
confere('mix rebalanceado guarda a escolha', rebalancear({ expedite: 8, standard: 70, fixed_date: 12, intangible: 10 }, 'expedite', 30).expedite, 30)

// ----------------------------------------------------------------- N3: eficiencia do cartao (aba Historico)
const mov = (de: string | null, segundos: number | null, quando: string) =>
  ({ id: quando, from_column_id: de, seconds_in_from_column: segundos, from_column_name: null, to_column_name: null, moved_at: quando, moved_by_name: null, note: null, is_backward: false })
const ef = eficienciaDoCartao(
  [mov(null, null, '2026-10-01T00:00:00Z'), mov('a-fazer', 86_400, '2026-10-02T00:00:00Z'), mov('fazendo', 3 * 86_400, '2026-10-05T00:00:00Z')],
  colunas,
  'aguardando',
  false,
  Date.parse('2026-10-06T00:00:00Z'),
)
confere('eficiencia: trabalho, espera (inclusive a coluna atual) e %', [ef.trabalhoS / 86_400, ef.esperaS / 86_400, Math.round(ef.pct ?? 0)], [3, 2, 60])
confere('eficiencia sem movimentos', eficienciaDoCartao([], colunas, 'entrada', false, 0).pct, null)

// ----------------------------------------------------------------- N3: administracao (travas da tela antiga)
const master = { id: 'm1', role: 'master' }
confere(
  'travas',
  [
    travaDoUsuario(master, { id: 'm1', role: 'master', is_active: true }, 2),
    travaDoUsuario({ id: 'a1', role: 'administrator' }, { id: 'm1', role: 'master', is_active: true }, 2),
    travaDoUsuario(master, { id: 'm2', role: 'master', is_active: true }, 1),
    travaDoUsuario(master, { id: 'e1', role: 'editor', is_active: true }, 1),
  ],
  ['admin.trava_voce', 'admin.trava_master', 'admin.trava_ultimo_master', null],
)

if (falhas > 0) throw new Error(`${falhas} de ${total} verificacoes falharam.`)
console.log(`${total} verificacoes das telas do Nagare passaram.`)
