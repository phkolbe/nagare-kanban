/**
 * Metricas de fluxo de um quadro (paridade com a tela antiga, N3): 9 indicadores, CFD,
 * dispersao, vazao, idade do WIP, eficiencia de fluxo, distribuicao, bloqueios, mix de classes,
 * Lei de Little, a previsao Monte Carlo (18/10/2026) e o KMM. Cada painel diz como ler e o que
 * e saudavel. Os textos que vem da API ja chegam na lingua da pessoa.
 */

import { useState, type ReactNode } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { Aviso, Cabecalho, Carregando, ErroDeApi, Tag, hojeISO } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { CLASSES, type ClasseServico, type SegmentoVisao } from '../../api/tipos'
import { SeletorIdioma } from '../../componentes/SeletorIdioma'
import {
  AreaEmpilhada,
  Barras,
  BarrasHorizontais,
  COR_CLASSE,
  Dispersao,
  FORMA_CLASSE,
  Histograma,
  Medidor,
  PontosPorColuna,
  Rosca,
  corDoCfd,
  type Percentil,
} from '../../graficos/Graficos'
import { ms } from '../../graficos/escalas'
import { useT, type Chave } from '../../i18n'
import { useProjetos, useQuadro, useQuadros, useVinculo } from '../quadro/consultas'
import {
  flagDoPainel,
  PERIODOS,
  useMetrica,
  type FiltrosMetricas,
  type Kmm,
  type Painel,
  type Resumo,
  type Series,
  type ValorMetrica,
} from './consultas'
import { formatarIndicador } from './formato'
import { PainelKmm } from './PainelKmm'
import { PainelPrevisao } from './PainelPrevisao'

const TOM_PERCENTIL: Record<string, Percentil['tom']> = { p50: 'info', p70: 'sucesso', p85: 'alerta', p95: 'perigo' }

export function TelaMetricas() {
  const { quadroId } = useParams()
  const quadros = useQuadros()
  const vinculo = useVinculo()
  const { t } = useT()
  if (quadroId) return <MetricasDoQuadro key={quadroId} quadroId={quadroId} />
  if (quadros.isPending || vinculo.isPending) return <Carregando texto={t('comum.carregando')} />
  if (quadros.isError) return <ErroDeApi erro={quadros.error} />
  const padrao = vinculo.data?.link?.board_id ?? quadros.data.items.find((q) => !q.archived)?.id
  return padrao ? <Navigate to={padrao} replace /> : <Aviso tipo="info">{t('quadro.sem_quadro')}</Aviso>
}

const INDICADORES: { chave: string; rotulo: Chave; avancado: boolean; principal?: boolean }[] = [
  { chave: 'p50', rotulo: 'metricas.lead_p50', avancado: false, principal: true },
  { chave: 'customer_lead_time_p85', rotulo: 'metricas.lead_p85', avancado: false },
  { chave: 'system_lead_time', rotulo: 'metricas.ciclo', avancado: false },
  { chave: 'throughput', rotulo: 'metricas.vazao', avancado: false, principal: true },
  { chave: 'wip', rotulo: 'metricas.wip', avancado: false, principal: true },
  { chave: 'flow_efficiency', rotulo: 'metricas.eficiencia', avancado: true },
  { chave: 'due_date_performance', rotulo: 'metricas.prazos', avancado: false },
  { chave: 'sle_attainment', rotulo: 'metricas.sle', avancado: true },
  { chave: 'failure_demand', rotulo: 'metricas.falha', avancado: false },
]

function MetricasDoQuadro({ quadroId }: { quadroId: string }) {
  const { t, eu, numero, data } = useT()
  const { rota } = useNagare()
  const navegar = useNavigate()
  const quadros = useQuadros()
  const quadro = useQuadro(quadroId)
  const projetos = useProjetos()
  const hoje = hojeISO()
  const flags = eu?.flags ?? {}
  const segmentos: SegmentoVisao[] = [
    ...(flags.unified_flow !== false ? (['unified'] as const) : []),
    ...(flags.upstream_board !== false ? (['upstream'] as const) : []),
    ...(flags.downstream_board !== false ? (['downstream'] as const) : []),
  ]
  const [filtros, setFiltros] = useState<FiltrosMetricas>({
    periodo: '90d',
    projeto: '',
    classe: '',
    segmento: segmentos[0] ?? 'unified',
  })
  const [cfdEntregues, setCfdEntregues] = useState(true)
  const criacao = quadro.data?.created_at ?? null
  const ligado = (p: Painel) => flags[flagDoPainel(p)] !== false && quadro.isSuccess
  const m = <T,>(p: Painel) => useMetrica<T>(quadroId, p, filtros, hoje, criacao, ligado(p))
  const resumo = m<Resumo>('summary')
  const vazao = m<Series>('throughput')
  const distribuicao = m<Series>('distribution')
  const cfd = m<Series>('cfd')
  const dispersao = m<Series>('scatter')
  const idade = m<Series>('aging')
  const eficiencia = m<Series>('flow-efficiency')
  const bloqueios = m<Series>('blockers')
  const demanda = m<Series>('demand')
  const little = m<Series>('littles-law')
  const kmm = m<Kmm>('kmm')

  if (quadro.isPending) return <Carregando texto={t('comum.carregando')} />
  if (quadro.isError) return <ErroDeApi erro={quadro.error} />
  const q = quadro.data
  const dias = (v: number) => `${numero(v, v < 10 ? 1 : 0)} ${t('unidade.dias')}`
  const percentis = (s: Series | undefined): Percentil[] =>
    Object.entries(s?.percentiles ?? {}).map(([chave, valor]) => ({ rotulo: `P${chave.slice(1)}`, valor, tom: TOM_PERCENTIL[chave] ?? 'info' }))
  const classes = CLASSES.map((c) => ({ chave: c, rotulo: t(`classe.${c}`), cor: COR_CLASSE[c]!, forma: FORMA_CLASSE[c] }))

  return (
    <div className="ng-metricas">
      <Cabecalho
        kicker={t('metricas.kicker')}
        titulo={q.name}
        acoes={
          <>
            <label className="ng-seletor-quadro">
              <span className="visualmente-oculto">{t('quadro.trocar')}</span>
              <select value={quadroId} aria-label={t('quadro.trocar')} onChange={(e) => navegar(rota(`metricas/${e.target.value}`))}>
                {(quadros.data?.items ?? [])
                  .filter((x) => !x.archived)
                  .map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                    </option>
                  ))}
              </select>
            </label>
            <SeletorIdioma />
          </>
        }
      />
      <div className="ng-barra ng-barra-metricas">
        <div className="abas" role="tablist" aria-label={t('metricas.periodo')}>
          {PERIODOS.map((p) => (
            <button key={p.chave} type="button" role="tab" aria-selected={filtros.periodo === p.chave} onClick={() => setFiltros({ ...filtros, periodo: p.chave })}>
              {t(`metricas.periodo.${p.chave}`)}
            </button>
          ))}
        </div>
        {flags.multi_project !== false && (
          <select aria-label={t('filtros.projeto')} value={filtros.projeto} onChange={(e) => setFiltros({ ...filtros, projeto: e.target.value })}>
            <option value="">{t('metricas.todos_projetos')}</option>
            {(projetos.data?.items ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.key} · {p.name}
              </option>
            ))}
          </select>
        )}
        {flags.service_classes !== false && (
          <select aria-label={t('filtros.classe')} value={filtros.classe} onChange={(e) => setFiltros({ ...filtros, classe: e.target.value as ClasseServico | '' })}>
            <option value="">{t('metricas.todas_classes')}</option>
            {CLASSES.map((c) => (
              <option key={c} value={c}>
                {t(`classe.${c}`)}
              </option>
            ))}
          </select>
        )}
        {segmentos.length > 1 && (
          <div className="abas" role="tablist" aria-label={t('quadro.segmento')}>
            {segmentos.map((s) => (
              <button key={s} type="button" role="tab" aria-selected={filtros.segmento === s} onClick={() => setFiltros({ ...filtros, segmento: s })}>
                {t(`segmento.${s}`)}
              </button>
            ))}
          </div>
        )}
        <span className="ng-pilula" title={t('metricas.envelope_dica')}>
          {q.count_envelope_in_metrics ? t('metricas.envelope_contado') : t('metricas.envelope_fora')}
        </span>
      </div>
      <p className="mini">{t('metricas.cada_painel')}</p>

      <section className="ng-indicadores" aria-label={t('metricas.indicadores')}>
        {resumo.isPending && ligado('summary') ? (
          <Carregando texto={t('comum.carregando')} />
        ) : resumo.isError ? (
          <ErroDeApi erro={resumo.error} />
        ) : (
          INDICADORES.map((ind) => {
            const fechado = ind.avancado && flags.metrics_advanced === false
            const valor: ValorMetrica | undefined =
              ind.chave === 'p50'
                ? resumo.data
                  ? {
                      value: resumo.data.percentiles.p50 ?? null,
                      unit: 'days',
                      trend_pct: null,
                      sample_size: resumo.data.sample_size,
                      previous_value: null,
                      low_confidence: resumo.data.low_confidence,
                      label: null,
                      description: t('metricas.lead_p50_dica'),
                      higher_is_better: false,
                    }
                  : undefined
                : resumo.data?.metrics[ind.chave]
            return <Indicador key={ind.chave} rotulo={t(ind.rotulo)} valor={valor} principal={ind.principal} fechado={fechado} />
          })
        )}
      </section>

      <div className="ng-paineis">
        <PainelDe painel="cfd" consulta={cfd} largo>
          {(d) => {
            // Uma cor por etapa na ordem do fluxo; a entrega num neutro (18/10/2026).
            const familia = (s: (typeof d.series)[number]) =>
              s.is_delivery_point || s.column_kind === 'done' ? 'entrega' : s.segment === 'upstream' ? 'upstream' : 'downstream'
            const cores = new Map<string, string>()
            d.series.filter((s) => familia(s) !== 'entrega').forEach((s, i) => cores.set(s.key, corDoCfd(i)))
            const entrega = d.series.find((s) => familia(s) === 'entrega')
            const comprometidas = d.series.filter((s) => familia(s) === 'downstream')
            // Entregues do periodo (18/10/2026): a faixa da entrega comeca em zero no inicio da
            // janela, como na leitura padrao do CFD, e cresce na inclinacao da vazao; a chave
            // "Mostrar entregues" tira a faixa para ver so o trabalho em andamento.
            const valores = (s: (typeof d.series)[number]) => {
              const v = s.points.map((p) => Number(p.y) || 0)
              if (familia(s) !== 'entrega') return v
              const base = v[0] ?? 0
              return v.map((n) => Math.max(0, n - base))
            }
            const rotulo = (s: (typeof d.series)[number]) =>
              familia(s) === 'entrega' ? t('metricas.cfd_entregue_periodo', { nome: s.label ?? s.key }) : (s.label ?? s.key)
            const visiveis = d.series.filter((s) => cfdEntregues || familia(s) !== 'entrega')
            const series = [...visiveis].reverse()
            const x = (d.series[0]?.points ?? []).map((p) => ms(String(p.t ?? p.x)))
            // Lei de Little no cursor: vazao das ultimas 4 semanas (entregas acumuladas na data
            // menos as de 28 dias antes, ou desde o inicio da janela), WIP comprometido (do ponto
            // de compromisso ate antes da entrega) e lead time = WIP / vazao. Contas lineares,
            // feitas uma vez por desenho.
            const acumuladas = entrega?.points.map((p) => Number(p.y) || 0) ?? []
            const wip = x.map((_, i) => comprometidas.reduce((soma, s) => soma + (Number(s.points[i]?.y) || 0), 0))
            const QUATRO_SEMANAS = 28 * 86_400_000
            let inicio = 0
            const little = x.map((agora, i) => {
              while (inicio < i && x[inicio]! < agora - QUATRO_SEMANAS) inicio += 1
              const semanas = (agora - x[inicio]!) / (7 * 86_400_000)
              if (!entrega || semanas <= 0) return null
              const vazao = ((acumuladas[i] ?? 0) - (acumuladas[inicio] ?? 0)) / semanas
              const lead = vazao > 0 ? (wip[i]! / vazao) * 7 : null
              return t('metricas.cfd_little', {
                vazao: numero(vazao, 1),
                wip: numero(wip[i]!, 0),
                lead: lead === null ? '—' : dias(lead),
              })
            })
            // A linha do compromisso: no topo da primeira etapa comprometida (as series vao de baixo para cima).
            const limite = comprometidas[0] ? { chave: comprometidas[0].key, rotulo: t('quadro.compromisso') } : undefined
            return (
              <AreaEmpilhada
                titulo={t('metricas.cfd')}
                limite={limite}
                extra={(i) => little[i] ?? null}
                nota={t('metricas.cfd_nota')}
                acoes={
                  <label className="ng-alternar mini">
                    <input type="checkbox" checked={cfdEntregues} onChange={(e) => setCfdEntregues(e.target.checked)} />
                    {t('metricas.cfd_mostrar_entregues')}
                  </label>
                }
                x={x}
                rotuloY={d.y_label ?? undefined}
                formatarX={(v) => data(new Date(v).toISOString())}
                series={series.map((s) => ({
                  chave: s.key,
                  rotulo: rotulo(s),
                  valores: valores(s),
                  cor: cores.get(s.key) ?? 'var(--ng-cfd-entrega)',
                }))}
                tabela={{
                  colunas: [t('metricas.data'), ...visiveis.map(rotulo)],
                  linhas: x.map((v, i) => [data(new Date(v).toISOString()), ...visiveis.map((s) => valores(s)[i] ?? 0)]),
                }}
              />
            )
          }}
        </PainelDe>

        <PainelDe painel="scatter" consulta={dispersao}>
          {(d) => {
            const pontos = (d.series.find((s) => s.mark === 'dot')?.points ?? []).map((p) => ({
              x: ms(String(p.x)),
              y: Number(p.y),
              grupo: String(p.service_class ?? 'standard'),
              rotulo: `${String(p.key ?? '')} ${String(p.title ?? '')}`.trim(),
            }))
            return (
              <Dispersao
                titulo={t('metricas.dispersao')}
                nota={t('metricas.dispersao_nota')}
                pontos={pontos}
                grupos={classes}
                percentis={percentis(d)}
                formatarX={(v) => data(new Date(v).toISOString())}
                formatarY={dias}
                rotuloY={d.y_label ?? undefined}
                tabela={{ colunas: [t('metricas.cartao'), t('metricas.entregue'), t('metricas.dias')], linhas: pontos.map((p) => [p.rotulo, data(new Date(p.x).toISOString()), numero(p.y, 1)]) }}
              />
            )
          }}
        </PainelDe>

        <PainelDe painel="throughput" consulta={vazao}>
          {(d) => {
            const barras = d.series.find((s) => s.mark === 'bar') ?? d.series[0]
            const media = d.series.find((s) => s !== barras)
            const itens = (barras?.points ?? []).map((p) => ({ rotulo: data(String(p.x)).slice(0, 5), valor: Number(p.y) || 0 }))
            return (
              <Barras
                titulo={t('metricas.vazao_titulo')}
                nota={t('metricas.vazao_nota')}
                itens={itens}
                legendaBarras={barras?.label ?? undefined}
                linha={media ? { rotulo: media.label ?? '', valores: media.points.map((p) => (p.y == null ? null : Number(p.y))) } : undefined}
                rotuloY={d.y_label ?? undefined}
                formatarValor={(v) => numero(v, v % 1 ? 1 : 0)}
                tabela={{ colunas: [t('metricas.periodo'), barras?.label ?? ''], linhas: itens.map((i) => [i.rotulo, i.valor]) }}
              />
            )
          }}
        </PainelDe>

        <PainelDe painel="aging" consulta={idade}>
          {(d) => {
            const colunas = (d.categories as { id: string; name: string; count?: number; wip_max?: number | null }[]).map((c) => ({
              id: c.id,
              nome: c.name,
              sub: c.wip_max ? `${c.count ?? 0}/${c.wip_max}` : String(c.count ?? 0),
            }))
            const pontos = (d.series.find((s) => s.key === 'cards')?.points ?? []).map((p) => ({
              coluna: String(p.column_id ?? p.x),
              y: Number(p.y),
              grupo: String(p.service_class ?? 'standard'),
              rotulo: `${String(p.key ?? '')} ${String(p.title ?? '')}`.trim(),
            }))
            return (
              <PontosPorColuna
                titulo={t('metricas.idade')}
                nota={t('metricas.idade_nota')}
                colunas={colunas}
                pontos={pontos}
                referencias={percentis(d)}
                grupos={classes}
                formatarY={dias}
                rotuloY={d.y_label ?? undefined}
                tabela={{ colunas: [t('metricas.cartao'), t('metricas.coluna'), t('metricas.dias')], linhas: pontos.map((p) => [p.rotulo, colunas.find((c) => c.id === p.coluna)?.nome ?? '', numero(p.y, 1)]) }}
              />
            )
          }}
        </PainelDe>

        <PainelDe painel="flow-efficiency" consulta={eficiencia} largo>
          {(d) => {
            const meta = d.meta as {
              flow_efficiency?: number
              touch_days?: number
              wait_days?: number
              envelope_days?: number
              include_envelope?: boolean
              interpretation?: string
              by_column?: { column_name: string; avg_days_per_card: number; share_of_lead_time_pct: number; kind: string }[]
            }
            const entregues = d.sample_size || 1
            const series = [
              { chave: 'touch', rotulo: t('metricas.toque'), cor: 'var(--green)' },
              { chave: 'wait', rotulo: t('metricas.espera'), cor: 'var(--amber)' },
              ...(meta.include_envelope ? [{ chave: 'envelope', rotulo: t('metricas.envelope'), cor: 'var(--gold)' }] : []),
            ]
            const colunas = [...(meta.by_column ?? [])].sort((a, b) => b.avg_days_per_card - a.avg_days_per_card).slice(0, 10)
            return (
              <div className="ng-duas">
                <BarrasHorizontais
                  titulo={t('metricas.eficiencia_titulo')}
                  nota={
                    <>
                      {t('metricas.eficiencia_nota')}
                      {meta.interpretation && (
                        <>
                          {' '}
                          <b>{meta.interpretation}</b>
                        </>
                      )}
                    </>
                  }
                  series={series}
                  formatarValor={dias}
                  linhas={[
                    {
                      rotulo: t('metricas.item_medio'),
                      segmentos: [
                        { chave: 'touch', valor: (meta.touch_days ?? 0) / entregues },
                        { chave: 'wait', valor: (meta.wait_days ?? 0) / entregues },
                        ...(meta.include_envelope ? [{ chave: 'envelope', valor: (meta.envelope_days ?? 0) / entregues }] : []),
                      ],
                      nota: t('metricas.toque_pct', { pct: numero(meta.flow_efficiency ?? 0, 0) }),
                    },
                  ]}
                />
                <BarrasHorizontais
                  titulo={t('metricas.onde_acumula')}
                  nota={t('metricas.onde_acumula_nota')}
                  series={[{ chave: 'dias', rotulo: t('metricas.dias_por_cartao'), cor: 'var(--ng-rampa-b)' }]}
                  formatarValor={dias}
                  linhas={colunas.map((c) => ({
                    rotulo: c.column_name,
                    segmentos: [{ chave: 'dias', valor: c.avg_days_per_card }],
                    nota: t('metricas.do_lead_time', { pct: numero(c.share_of_lead_time_pct, 0) }),
                  }))}
                  tabela={{ colunas: [t('metricas.coluna'), t('metricas.dias_por_cartao'), '%'], linhas: colunas.map((c) => [c.column_name, numero(c.avg_days_per_card, 1), numero(c.share_of_lead_time_pct, 0)]) }}
                />
              </div>
            )
          }}
        </PainelDe>

        <PainelDe painel="distribution" consulta={distribuicao}>
          {(d) => {
            const faixasDeValor = (d.series[0]?.points ?? []).map((p) => ({ de: Number(p.x), ate: Number(p.x_end ?? p.x), contagem: Number(p.y) }))
            return (
              <Histograma
                titulo={t('metricas.distribuicao')}
                nota={t('metricas.distribuicao_nota')}
                faixasDeValor={faixasDeValor}
                marcadores={percentis(d).map((p) => ({ rotulo: p.rotulo, valor: p.valor, destaque: p.rotulo === 'P85' }))}
                formatarX={(v) => numero(v, 0)}
                rotuloY={d.y_label ?? undefined}
                tabela={{ colunas: [t('metricas.faixa_dias'), t('metricas.itens')], linhas: faixasDeValor.map((f) => [`${numero(f.de, 0)}–${numero(f.ate, 0)}`, f.contagem]) }}
              />
            )
          }}
        </PainelDe>

        <PainelDe painel="blockers" consulta={bloqueios}>
          {(d) => {
            const pareto = ((d.meta as { pareto?: { category: string; label: string; days: number; count: number; share_pct: number }[] }).pareto ?? [])
            return (
              <BarrasHorizontais
                titulo={t('metricas.bloqueios')}
                nota={t('metricas.bloqueios_nota')}
                series={[{ chave: 'dias', rotulo: t('metricas.dias_bloqueado'), cor: 'var(--red)' }]}
                formatarValor={dias}
                linhas={pareto.map((p) => ({
                  rotulo: t(`bloqueio.categoria.${p.category}` as Chave),
                  segmentos: [{ chave: 'dias', valor: p.days }],
                  nota: `${numero(p.share_pct, 0)}% · ${p.count}×`,
                }))}
                textoVazio={t('metricas.sem_bloqueios')}
                tabela={{ colunas: [t('bloqueio.categoria'), t('metricas.dias'), t('metricas.vezes')], linhas: pareto.map((p) => [t(`bloqueio.categoria.${p.category}` as Chave), numero(p.days, 1), p.count]) }}
              />
            )
          }}
        </PainelDe>

        <PainelDe painel="demand" consulta={demanda}>
          {(d) => {
            const meta = d.meta as { delivered_total?: number; service_class_mix?: { key: string; count: number }[] }
            return (
              <Rosca
                titulo={t('metricas.mix')}
                nota={t('metricas.mix_nota')}
                valorCentro={numero(meta.delivered_total ?? 0, 0)}
                rotuloCentro={t('metricas.entregues')}
                fatias={(meta.service_class_mix ?? []).map((f) => ({
                  chave: f.key,
                  rotulo: t(`classe.${f.key as ClasseServico}`),
                  valor: f.count,
                  cor: COR_CLASSE[f.key] ?? 'var(--navy)',
                  forma: FORMA_CLASSE[f.key],
                }))}
              />
            )
          }}
        </PainelDe>

        <PainelDe painel="littles-law" consulta={little}>
          {(d) => {
            const meta = d.meta as {
              coherence_ratio?: number
              stable?: boolean
              verdict?: string
              avg_wip?: number
              throughput_per_week?: number
              avg_system_lead_time_days?: number
            }
            if (meta.coherence_ratio == null) return <p className="mini">{t('metricas.little_vazio')}</p>
            return (
              <Medidor
                titulo={t('metricas.little')}
                nota={t('metricas.little_nota')}
                valor={meta.coherence_ratio}
                min={0}
                max={2}
                alvo={1}
                rotuloValor={`${numero(meta.coherence_ratio, 2)}×`}
                tom={meta.stable ? 'sucesso' : 'alerta'}
                rodape={
                  <div>
                    {meta.verdict && <p>{meta.verdict}</p>}
                    <dl className="ng-numeros">
                      <div>
                        <dt>{t('metricas.wip_medio')}</dt>
                        <dd>{numero(meta.avg_wip ?? 0, 1)}</dd>
                      </div>
                      <div>
                        <dt>{t('metricas.vazao_semana')}</dt>
                        <dd>{numero(meta.throughput_per_week ?? 0, 1)}</dd>
                      </div>
                      <div>
                        <dt>{t('metricas.ciclo_medio')}</dt>
                        <dd>{dias(meta.avg_system_lead_time_days ?? 0)}</dd>
                      </div>
                    </dl>
                  </div>
                }
              />
            )
          }}
        </PainelDe>

        <PainelPrevisao quadroId={quadroId} />
      </div>

      <section className="card ng-kmm" aria-label={t('kmm.titulo')}>
        <h2 className="ng-subtitulo">{t('kmm.titulo')}</h2>
        {flags.kmm_dashboard === false ? (
          <Bloqueado flag="kmm_dashboard" />
        ) : kmm.isPending ? (
          <Carregando texto={t('comum.carregando')} />
        ) : kmm.isError ? (
          <ErroDeApi erro={kmm.error} />
        ) : (
          <PainelKmm kmm={kmm.data} />
        )}
      </section>
    </div>
  )
}

function Indicador({ rotulo, valor, principal, fechado }: { rotulo: string; valor: ValorMetrica | undefined; principal?: boolean; fechado: boolean }) {
  const { t, numero } = useT()
  if (fechado) {
    return (
      <div className="ng-indicador fechado" title={t('metricas.plano_avancado')}>
        <div className="ng-indicador-rotulo">{rotulo}</div>
        <div className="ng-indicador-valor">🔒</div>
        <div className="mini">{t('metricas.plano_avancado')}</div>
      </div>
    )
  }
  const { valor: texto, unidade } = formatarIndicador(valor?.value, valor?.unit ?? '', numero, {
    dias: t('unidade.dias'),
    porSemana: t('unidade.por_semana'),
  })
  const melhora = valor?.trend_pct != null && valor.higher_is_better != null ? (valor.trend_pct > 0) === valor.higher_is_better : null
  return (
    <div className={`ng-indicador${principal ? ' principal' : ''}`} title={valor?.description ?? undefined}>
      <div className="ng-indicador-rotulo">
        {rotulo}
        {principal && <Tag cor="azul">{t('metricas.lei_de_little')}</Tag>}
      </div>
      <div className="ng-indicador-valor">
        {texto}
        {unidade && <span className="ng-indicador-unidade">{unidade}</span>}
      </div>
      {valor?.value == null ? (
        <div className="mini">{t('metricas.sem_entregas')}</div>
      ) : (
        <div className="mini ng-indicador-pe">
          {valor.trend_pct != null && (
            <span className={melhora === null ? '' : melhora ? 'ng-bom' : 'ng-ruim'}>
              {valor.trend_pct > 0 ? '▲' : valor.trend_pct < 0 ? '▼' : '•'} {numero(Math.abs(valor.trend_pct), 0)}%
            </span>
          )}
          <span>n={valor.sample_size}</span>
          {valor.low_confidence && <Tag cor="amarelo">{t('metricas.pouca_confianca')}</Tag>}
        </div>
      )}
    </div>
  )
}

/** Painel bloqueado pelo plano: diz qual recurso falta, sem tela de compra. */
export function Bloqueado({ flag }: { flag: string }) {
  const { t, eu } = useT()
  const { modo, rota } = useNagare()
  const navegar = useNavigate()
  return (
    <div className="ng-bloqueado">
      <span aria-hidden="true">🔒</span>
      <p>{t('metricas.bloqueado', { recurso: t(`flag.${flag}` as Chave) })}</p>
      {modo === 'sozinho' && eu?.capabilities.manage_plans_flags && (
        <button type="button" className="link" onClick={() => navegar(rota('admin/flags'))}>
          {t('admin.flags')}
        </button>
      )}
    </div>
  )
}

function PainelDe({
  painel,
  consulta,
  largo = false,
  children,
}: {
  painel: Painel
  consulta: { isPending: boolean; isError: boolean; error: unknown; data?: Series; refetch: () => unknown; isFetching: boolean }
  largo?: boolean
  children: (dados: Series) => ReactNode
}) {
  const { t, eu } = useT()
  const flag = flagDoPainel(painel)
  let corpo: ReactNode
  if (eu?.flags[flag] === false) corpo = <Bloqueado flag={flag} />
  else if (consulta.isPending) corpo = <Carregando texto={t('comum.carregando')} />
  else if (consulta.isError)
    corpo = (
      <>
        <ErroDeApi erro={consulta.error} />
        <button type="button" className="btn sm ghost" onClick={() => consulta.refetch()}>
          {t('comum.tentar_de_novo')}
        </button>
      </>
    )
  else corpo = children(consulta.data!)
  return <section className={`card ng-painel${largo ? ' largo' : ''}${consulta.isFetching ? ' ocupado' : ''}`}>{corpo}</section>
}
