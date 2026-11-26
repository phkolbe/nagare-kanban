/**
 * Verificacoes das telas do Nagare (modulo Tarefas) — `npm run check` em `web/`.
 *
 * As decisoes da tela vivem em funcoes puras (regras do quadro, fases, textos) e aqui sao
 * CHAMADAS com dados no formato real da API do Nagare.
 */

import { horasEmTexto, minutosDeTexto } from '../features/cartao/horas'
import { periodoPadrao, textoNoPrazo } from '../features/metricas/PainelPessoas'
import { baseDoSozinho, exemploDeChave, modoDoCaminho, montarRota, rotaDoCartao } from '../app/contexto'
import { ehSoDoNagare } from '../componentes/Atalhos'
import type { Cartao, Coluna, SituacaoWip } from '../api/tipos'
import { travaDoUsuario } from '../features/admin/travas'
import { eficienciaDoCartao } from '../features/cartao/eficiencia'
import { janela } from '../features/metricas/consultas'
import { buscaDoIndicador, formatarIndicador, valorKmm } from '../features/metricas/formato'
import { filtrarDaCaixa, opcoesDaCaixa, proximoIndice, textoDeBusca } from '../componentes/listaComFiltro'
import { itensDePessoas, itensDeProjetos, itensDeQuadros, itensDoTrocadorDeQuadro, podeCriarProjeto } from '../features/projetos/listas'
import type { Projeto, Quadro } from '../api/tipos'
import { melhorLinha, rebalancear } from '../features/simulador/tipos'
import { eixoRedondo, passoRedondo } from '../graficos/escalas'
import { rotaDoLinkAntigo } from '../sozinho/linksAntigos'
import { tokenVencido, vencimentoDoToken } from '../api/sessaoLocal'
import { temaEfetivo } from '../sozinho/tema'
import { relatoriosNaoLidos } from '../sozinho/Sino'
import {
  buscaDosParametros,
  formatarValor,
  parametrosDaBusca,
  periodoVizinho,
  prontoParaPedir,
  proximoNoFuturo,
  textoDaVariacao,
  tomDaVariacao,
} from '../features/relatorios/formato'
import {
  corpoDaEmissao,
  enderecoDoLink,
  idiomaDoNavegador,
  linksAtivos,
  MODELOS_COM_LINK,
  OPCOES_DE_VALIDADE,
  parametrosDoRep09,
  situacaoDoErroPublico,
  VALIDADE_PADRAO,
} from '../features/relatorios/emitidos'
import { TRILHO, bloqueioDoItem } from '../sozinho/trilho'
import { faseDoCartao } from '../features/projetos/fase'
import {
  contarFiltros,
  destinosAFrente,
  faixasDeGrupo,
  filtrarNaTela,
  FILTROS_VAZIOS,
  gravarFiltros,
  lerFiltros,
  ordenarColunas,
  parametrosDaVisao,
  providenciaDo,
  situacaoDoPrazo,
  tempoDoCartao,
  validarDestino,
} from '../features/quadro/regras'
import { CATALOGOS, pt, type Chave } from '../i18n/mensagens'
import { itensDeCasos, rotuloDoCaso, SEM_CASO, termoDaBusca, vinculoDoCartao, vinculoDoCaso } from '../features/cartao/casos'
import { moverTipo, opcoesDeTipo, rotuloDoTipo, tipoInicial } from '../features/cartao/tiposDeCartao'
import type { CasoDoSrAdv, TipoDeCartao } from '../api/tipos'
import { chaveDoErro, interpolar, traducao } from '../i18n/texto'
import { deveRecarregar, marcarArrasto, quandoPuder, segundosDesde } from '../features/quadro/atualizacao'

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
// Selo "12 d" e barra (24/11/2026): verde ate 70% do SLE, ambar ate o SLE, vermelho alem.
confere('tempo sem idade', tempoDoCartao(null, 25), null)
confere('tempo sem SLE: selo neutro', tempoDoCartao(10.4, null), { dias: 10, situacao: 'neutro', fracao: null, pct: null })
confere('tempo: 12 de 25 dias e 48%', tempoDoCartao(12, 25)?.pct, 48)
confere(
  'tempo: ok, atencao (70% e no SLE), risco (alem)',
  [tempoDoCartao(17, 25), tempoDoCartao(17.5, 25), tempoDoCartao(25, 25), tempoDoCartao(25.4, 25)].map((x) => x?.situacao),
  ['ok', 'atencao', 'atencao', 'risco'],
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
confere('chave de exemplo pela sigla do produto (SR GJ 4.6)', ['sr-gj', 'sr-adv', 'batoom', 'nagare'].map(exemploDeChave), ['SRGJ', 'SRADV', 'BATOOM', 'PROJ'])
confere('base do Nagare sozinho', [baseDoSozinho('/nagare/quadro'), baseDoSozinho('/quadro'), baseDoSozinho('/nagarex')], ['/nagare', '', ''])
confere('rotas nos dois modos', [montarRota('plataforma', 'sr-adv', 'quadro/1'), montarRota('sozinho', 'nagare', 'quadro/1'), montarRota('sozinho', 'nagare', ''), montarRota('sozinho', 'nagare', 'admin/usuarios'), montarRota('sozinho', 'nagare', 'metricas/q1')], [
  '/sr-adv/tarefas/quadro/1',
  '/board/1',
  '/',
  '/admin/users',
  '/metrics/q1',
])
confere(
  'endereco do cartao pela chave (24/11/2026)',
  [
    rotaDoCartao('sozinho', 'nagare', 'PPLAT-404'),
    rotaDoCartao('sozinho', 'sr-adv', 'SRADV-101'),
    rotaDoCartao('plataforma', 'sr-adv', 'SRADV-101'),
    montarRota('sozinho', 'nagare', 'cartao/PPLAT-404'),
  ],
  ['/card/PPLAT-404', '/sr-adv/card/SRADV-101', '/sr-adv/tarefas/cartao/SRADV-101', '/card/PPLAT-404'],
)
confere('links da tela antiga', ['#/board/abc', '#/metrics/q1', '#/admin/plans', '#/login', '#/x'].map(rotaDoLinkAntigo), [
  '/board/abc',
  '/metrics/q1',
  '/admin/plans',
  '/sign-in',
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
confere('o trilho tem as seis telas (Reports desde 24/11/2026)', TRILHO.map((i) => i.caminho), ['quadro', 'projetos', 'metricas', 'relatorios', 'simulador', 'admin'])

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

// Horas (GJ-4) e a leitura por pessoa (D45)
confere('horas em texto', [horasEmTexto(90), horasEmTexto(60), horasEmTexto(45)], ['1h30', '1h', '45min'])
confere('minutos do que se digita', ['1:30', '1h30', '1,5', '45min', '2', 'abc', '25:00'].map(minutosDeTexto), [90, 90, 90, 45, 120, null, null])
confere('no prazo', [textoNoPrazo({ on_time: 2, late: 1 }), textoNoPrazo({ on_time: 0, late: 0 })], ['2 de 3 (67%)', '—'])
confere('periodo padrao: do dia 1 a hoje', periodoPadrao(new Date(2026, 10, 6)), { de: '2026-11-01', ate: '2026-11-06' })

// ------------------------------------------- listas com filtro e numeros clicaveis (regra geral de 09/11/2026)
{
  const itens = [
    { id: 'p1', nome: 'SRA · Ação Cível' },
    { id: 'p2', nome: 'GJ · Gestão Jurídica', detalhe: 'sarah@exemplo.com' },
    { id: 'p3', nome: 'LOJ · Loja Batoom' },
  ]
  confere('lista: texto de busca', textoDeBusca('  Gestão   JURÍDICA '), 'gestao juridica')
  confere('lista: filtro sem acento e em qualquer ordem', filtrarDaCaixa(itens, 'civel acao').map((i) => i.id), ['p1'])
  confere('lista: filtro pelo detalhe', filtrarDaCaixa(itens, 'sarah').map((i) => i.id), ['p2'])
  confere('lista: opcao vazia so sem filtro', [opcoesDaCaixa(itens, '', true).length, opcoesDaCaixa(itens, 'loja', true).length, opcoesDaCaixa(itens, '', false).length], [4, 1, 3])
  confere('lista: setas param nas pontas', [proximoIndice(0, -1, 3), proximoIndice(2, 1, 3), proximoIndice(1, 1, 3), proximoIndice(0, 1, 0)], [0, 2, 2, 0])

  const projeto = (id: string, archived = false) => ({ id, key: id.toUpperCase(), name: `Projeto ${id}`, archived }) as Projeto
  const projetos = [projeto('a'), projeto('b', true), projeto('c')]
  confere('projetos: sem arquivado nem o excluido', itensDeProjetos(projetos, '', 'c').map((i) => i.nome), ['A · Projeto a'])
  confere('projetos: o escolhido arquivado continua', itensDeProjetos(projetos, 'b').map((i) => i.id), ['a', 'b', 'c'])
  const quadro = (id: string, archived = false, read_only = false) => ({ id, name: `Quadro ${id}`, archived, read_only }) as Quadro
  const quadros = [quadro('q1'), quadro('q2', true), quadro('q3', false, true)]
  confere('quadros: so os que recebem cartao', itensDeQuadros(quadros).map((i) => i.id), ['q1'])
  confere('trocador: so leitura no detalhe', itensDoTrocadorDeQuadro(quadros, 'só leitura').map((i) => [i.id, i.detalhe]), [['q1', null], ['q3', 'só leitura']])
  confere(
    'pessoas: e-mail no detalhe e o atual fora da lista',
    itensDePessoas([{ id: 'u1', name: 'Sarah', email: 's@x.com' }], { id: 'u9', nome: 'Antiga' }),
    [{ id: 'u1', nome: 'Sarah', detalhe: 's@x.com' }, { id: 'u9', nome: 'Antiga' }],
  )
  const eu = (manage_boards: boolean, multi_project = true) => ({ capabilities: { manage_boards }, flags: { multi_project } })
  confere(
    'novo projeto pelo +: gere quadros, usa projetos e nao e cadastro de fora',
    [podeCriarProjeto(eu(true), null), podeCriarProjeto(eu(false), null), podeCriarProjeto(eu(true, false), null), podeCriarProjeto(eu(true), { projects_new_url: 'https://x' })],
    [true, false, false, false],
  )

  const filtros = { projeto: 'p1', classe: 'expedite' as const, segmento: 'unified' as const }
  confere('indicador WIP abre o quadro com os filtros', buscaDoIndicador('wip', filtros), 'segmento=unified&projeto=p1&classe=expedite')
  confere('indicador de falha filtra o tipo', buscaDoIndicador('failure_demand', { ...filtros, classe: '' }), 'segmento=unified&projeto=p1&tipo=failure')
  confere('indicador de prazos filtra a data fixa', buscaDoIndicador('due_date_performance', filtros), 'segmento=unified&projeto=p1&classe=fixed_date')
  confere('tempos e vazao nao tem lista', [buscaDoIndicador('p50', filtros), buscaDoIndicador('throughput', filtros)], [null, null])
  confere('o filtro do indicador o quadro le de volta', lerFiltros(new URLSearchParams(buscaDoIndicador('failure_demand', filtros)!)).tipo, 'failure')
}

// ----------------------------------------------- R5-D: tipos por produto e o caso do SR Adv
{
  const tPt = traducao('pt-BR').t
  const juridicos: TipoDeCartao[] = [
    { key: 'prazo', name: 'Prazo', position: 0, archived: false, builtin: false },
    { key: 'peticao', name: 'Petição', position: 1, archived: false, builtin: false },
    { key: 'feature', name: null, position: 5, archived: true, builtin: false },
    { key: 'audiencia', name: 'Audiência', position: 2, archived: false, builtin: false },
  ]
  confere('tipo com nome do produto', rotuloDoTipo('peticao', juridicos, tPt), 'Petição')
  confere('tipo sem nome: o rotulo das telas', rotuloDoTipo('feature', juridicos, tPt), 'Funcionalidade')
  confere('tipo desconhecido: a chave', rotuloDoTipo('reuniao', juridicos, tPt), 'reuniao')
  confere('sem lista: a padrao', rotuloDoTipo('debt', undefined, tPt), 'Dívida técnica')
  confere(
    'opcoes: em uso na ordem, sem os arquivados',
    opcoesDeTipo(juridicos, null, tPt).map((o) => o.valor),
    ['prazo', 'peticao', 'audiencia'],
  )
  confere(
    'o tipo arquivado do cartao continua na lista',
    opcoesDeTipo(juridicos, 'feature', tPt).map((o) => o.texto),
    ['Prazo', 'Petição', 'Audiência', 'Funcionalidade (arquivado)'],
  )
  confere('tipo fora da lista continua', opcoesDeTipo(juridicos, 'velho', tPt).at(-1), { valor: 'velho', texto: 'velho' })
  confere('cartao novo: o primeiro em uso', [tipoInicial(juridicos), tipoInicial(undefined)], ['prazo', 'feature'])
  confere('subir e descer', [moverTipo(['a', 'b', 'c'], 'c', -1), moverTipo(['a', 'b', 'c'], 'a', -1)], [['a', 'c', 'b'], ['a', 'b', 'c']])

  const caso: CasoDoSrAdv = {
    id: 'c1',
    codigo: 'Caso 07',
    titulo: null,
    rotulo: 'Revisional · Maria Silva',
    cliente_id: 'p1',
    cliente_nome: 'Maria Silva',
    numero_processo: '1000123-45.2026.8.26.0100',
    situacao: 'ativo',
  }
  confere('rotulo do caso: codigo e titulo (ou rotulo)', [rotuloDoCaso(caso), rotuloDoCaso({ ...caso, titulo: 'Laudêmio' })], [
    'Caso 07 · Revisional · Maria Silva',
    'Caso 07 · Laudêmio',
  ])
  // Como o SR Adv manda de verdade: o rotulo ja traz o codigo.
  confere('rotulo do caso sem repetir o codigo', rotuloDoCaso({ ...caso, rotulo: 'Caso 07 · Revisional · Maria Silva' }),
    'Caso 07 · Revisional · Maria Silva')
  confere('o cliente vem do caso', vinculoDoCaso(caso), {
    case_id: 'c1',
    case_label: 'Caso 07 · Revisional · Maria Silva',
    client_id: 'p1',
    client_name: 'Maria Silva',
  })
  confere(
    'itens: cliente e processo no detalhe; encerrado marcado; o do cartao fica',
    itensDeCasos([caso, { ...caso, id: 'c2', situacao: 'encerrado', numero_processo: null }], { ...SEM_CASO, case_id: 'c9', case_label: 'Caso 09' }, 'encerrado'),
    [
      { id: 'c9', nome: 'Caso 09', detalhe: null },
      { id: 'c1', nome: 'Caso 07 · Revisional · Maria Silva', detalhe: 'Maria Silva · 1000123-45.2026.8.26.0100' },
      { id: 'c2', nome: 'Caso 07 · Revisional · Maria Silva', detalhe: 'Maria Silva · encerrado' },
    ],
  )
  confere('cartao sem caso', vinculoDoCartao({}), SEM_CASO)
  confere('busca sem espaco sobrando', termoDaBusca('  maria   silva '), 'maria silva')

  const porCaso = lerFiltros(new URLSearchParams('caso=c1&cartao=x'))
  confere('o quadro le o caso da URL', porCaso.caso, 'c1')
  confere('o servidor filtra pelo caso', parametrosDaVisao(porCaso).case_id, 'c1')
  confere('o caso conta como filtro', contarFiltros(porCaso), 1)
  confere('o caso volta para a URL', gravarFiltros(new URLSearchParams(), { ...FILTROS_VAZIOS, caso: 'c1' }).toString(), 'caso=c1')
}

// Atualizacao automatica do quadro (23/11/2026).
confere('a primeira versao so guarda', deveRecarregar(null, 'v1'), false)
confere('versao igual nao recarrega', deveRecarregar('v1', 'v1'), false)
confere('versao nova recarrega', deveRecarregar('v1', 'v2'), true)
confere('sem resposta nao recarrega', deveRecarregar('v1', undefined), false)
confere('segundos em passos de 5', [segundosDesde(0, 3_999), segundosDesde(0, 5_000), segundosDesde(0, 14_000), segundosDesde(0, 61_000), segundosDesde(10_000, 0)], [0, 5, 10, 60, 0])
{
  const vezes: string[] = []
  quandoPuder(() => vezes.push('livre'))
  confere('sem arrasto, recarrega na hora', vezes, ['livre'])
  marcarArrasto(true)
  quandoPuder(() => vezes.push('primeira'))
  quandoPuder(() => vezes.push('segunda'))
  confere('durante o arrasto, espera', vezes, ['livre'])
  marcarArrasto(false)
  confere('ao soltar, recarrega uma vez (a ultima pedida)', vezes, ['livre', 'segunda'])
  marcarArrasto(false)
  confere('soltar de novo nao repete', vezes, ['livre', 'segunda'])
}

{
  // Sessao local (24/11/2026): a tela le o vencimento do token e vai ao login sozinha.
  const token = (exp: number) =>
    `cabeca.${btoa(JSON.stringify({ sub: 'u1', exp })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')}.assinatura`
  const agora = Date.UTC(2026, 10, 24, 18, 46) // ms
  confere('vencimento do token em ms', vencimentoDoToken(token(1795000000)), 1795000000 * 1000)
  confere(
    'token vencido, valido, sem exp e ilegivel',
    [tokenVencido(token(agora / 1000 - 1), agora), tokenVencido(token(agora / 1000 + 3600), agora), tokenVencido('a.e30.b', agora), tokenVencido('lixo', agora), tokenVencido(null, agora)],
    [true, false, false, false, false],
  )
}

{
  // Reports, onda 1 (24/11/2026): parametros na URL, periodos vizinhos e valores por unidade.
  const p = parametrosDaBusca(new URLSearchParams('period=month&board_id=b1&lixo=x'))
  confere('parametros da busca', p, { period: 'month', board_id: 'b1' })
  confere('periodo desconhecido vira semana', parametrosDaBusca(new URLSearchParams('period=ano')).period, 'week')
  confere('busca em ordem fixa, sem vazios', buscaDosParametros({ board_id: 'b1', period: 'week', date: '' }), '?period=week&board_id=b1')
  confere(
    'pronto para pedir',
    [
      prontoParaPedir({ scope: 'board' }, { period: 'week' }),
      prontoParaPedir({ scope: 'board' }, { period: 'week', board_id: 'b' }),
      prontoParaPedir({ scope: 'project' }, { period: 'week' }),
      prontoParaPedir({ scope: 'projects' }, { period: 'custom', from: '2026-11-01' }),
      prontoParaPedir({ scope: 'none' }, { period: 'month' }),
    ],
    [false, true, false, false, true],
  )
  const semana = { period: { kind: 'week' as const, from: '2026-11-16', to: '2026-11-22', label: '', timezone: 'UTC' } }
  const mes = { period: { kind: 'month' as const, from: '2026-01-01', to: '2026-01-31', label: '', timezone: 'UTC' } }
  const livre = { period: { kind: 'custom' as const, from: '2026-11-01', to: '2026-11-10', label: '', timezone: 'UTC' } }
  confere('semana anterior e seguinte', [periodoVizinho(semana, -1), periodoVizinho(semana, 1)], [{ date: '2026-11-09' }, { date: '2026-11-23' }])
  confere('mes anterior atravessa o ano', periodoVizinho(mes, -1), { date: '2025-12-01' })
  confere('intervalo livre do mesmo tamanho', periodoVizinho(livre, -1), { from: '2026-10-22', to: '2026-10-31' })
  confere('proximo no futuro', [proximoNoFuturo(semana, '2026-11-22'), proximoNoFuturo(semana, '2026-11-25')], [true, false])
  const f = {
    numero: (v: number, casas = 0) => v.toFixed(casas).replace('.', ','),
    data: (iso: string | null | undefined) => (iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) : '—'),
    dataHora: (iso: string | null | undefined) => (iso ? 'dh' : '—'),
    dias: 'd',
    horas: 'h',
    porSemana: '/sem',
  }
  confere(
    'valores por unidade',
    [
      formatarValor(4.5, 'days', f),
      formatarValor(36.3, 'days', f),
      formatarValor(85, 'percent', f),
      formatarValor(4.52, 'cards/week', f),
      formatarValor(3.25, 'hours', f),
      formatarValor(0.83, 'ratio', f),
      formatarValor(null, 'count', f),
      formatarValor({ key: 'NGR-1', title: 'Fazer' }, 'card', f),
      formatarValor('2026-11-20', 'date', f),
    ],
    ['4,5 d', '36 d', '85%', '4,5/sem', '3,3 h', '0,83', '—', 'NGR-1 — Fazer', '20/11'],
  )
  confere('variacao em texto', [textoDaVariacao(12.4, f.numero), textoDaVariacao(-3.5, f.numero), textoDaVariacao(null, f.numero)], ['+12%', '−3,5%', ''])
  confere(
    'tom da variacao',
    [
      tomDaVariacao({ trend_pct: 10, higher_is_better: true }),
      tomDaVariacao({ trend_pct: 10, higher_is_better: false }),
      tomDaVariacao({ trend_pct: -5, higher_is_better: false }),
      tomDaVariacao({ trend_pct: 10, higher_is_better: null }),
      tomDaVariacao({ trend_pct: null, higher_is_better: true }),
    ],
    ['bom', 'ruim', 'bom', 'neutro', 'neutro'],
  )
  confere(
    'contador do item Reports: so avisos de relatorio nao lidos',
    relatoriosNaoLidos([
      { kind: 'report_issued', read_at: null },
      { kind: 'report_issued', read_at: '2026-11-24T10:00:00Z' },
      { kind: 'card_blocked', read_at: null },
    ]),
    1,
  )
  const itemReports = TRILHO.find((i) => i.caminho === 'relatorios')
  confere('item Reports no trilho, fechado sem plano ou sem papel', [
    itemReports ? bloqueioDoItem(itemReports, { reports: false }, { read_reports: true }) : 'sem item',
    itemReports ? bloqueioDoItem(itemReports, { reports: true }, { read_reports: false }) : 'sem item',
    itemReports ? bloqueioDoItem(itemReports, { reports: true }, { read_reports: true }) : 'sem item',
  ], ['plano', 'papel', null])
  confere('rota dos Reports em ingles', montarRota('sozinho', 'nagare', 'relatorios/hours'), '/reports/hours')
}

{
  // Reports, onda 2 (25/11/2026): emitir, link de cliente, pagina do cliente e o atalho do REP-09.
  confere(
    'corpo da emissao: o modelo e os parametros da tela, sem os vazios',
    corpoDaEmissao('hours', { period: 'month', date: '2026-10-01', project_ids: 'p1,p2', board_id: '' }),
    { template: 'hours', period: 'month', date: '2026-10-01', project_ids: 'p1,p2' },
  )
  confere('rotas dos emitidos em ingles', [montarRota('sozinho', 'nagare', 'relatorios/emitidos'), montarRota('sozinho', 'nagare', 'relatorios/emitidos/abc')], [
    '/reports/issued',
    '/reports/issued/abc',
  ])
  confere(
    'endereco do link de cliente, com e sem a base /nagare',
    [enderecoDoLink('https://sr03:8443', '/nagare', 'a-b_c'), enderecoDoLink('http://localhost:8770/', '', 'x y')],
    ['https://sr03:8443/nagare/r/a-b_c', 'http://localhost:8770/r/x%20y'],
  )
  confere(
    'o que dizer ao cliente quando o link nao abre',
    [
      situacaoDoErroPublico(410, 'report_link_expired'),
      situacaoDoErroPublico(410, 'report_link_revoked'),
      situacaoDoErroPublico(404, 'report_link_not_found'),
      situacaoDoErroPublico(429, 'too_many_requests'),
      situacaoDoErroPublico(403, 'feature_disabled'),
    ],
    ['vencido', 'revogado', 'inexistente', 'limite', 'erro'],
  )
  confere('REP-09 da previsao recem-registrada: os 30 dias ate hoje', parametrosDoRep09('q1', '2026-11-25'), {
    period: 'custom',
    from: '2026-10-27',
    to: '2026-11-25',
    board_id: 'q1',
  })
  confere(
    'lingua da pagina do cliente antes do relatorio chegar',
    [idiomaDoNavegador('en', 'pt-BR'), idiomaDoNavegador(null, 'pt-PT'), idiomaDoNavegador('xx', 'de-DE'), idiomaDoNavegador(null, undefined)],
    ['en', 'pt-BR', 'en', 'en'],
  )
  confere('links ativos', linksAtivos([{ state: 'active' }, { state: 'expired' }, { state: 'revoked' }, { state: 'active' }]), 2)
  confere('so REP-02 e REP-03 vao para o cliente; 7 dias por padrao', [MODELOS_COM_LINK, OPCOES_DE_VALIDADE.includes(VALIDADE_PADRAO), VALIDADE_PADRAO], [
    ['project_status', 'hours'],
    true,
    7,
  ])
}

if (falhas > 0) throw new Error(`${falhas} de ${total} verificacoes falharam.`)
console.log(`${total} verificacoes das telas do Nagare passaram.`)
