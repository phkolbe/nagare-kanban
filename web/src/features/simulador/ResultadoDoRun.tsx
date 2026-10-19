/** O resultado de um run: previsao Monte Carlo, experimento de WIP/replay ou mix de classes. */

import { Aviso, Tag } from '@pampufa/ui'

import { Barras, Histograma } from '../../graficos/Graficos'
import { useT } from '../../i18n'
import { melhorLinha, vazaoDa, type ResultadoExperimento, type ResultadoMonteCarlo, type Run } from './tipos'

export function ResultadoDoRun({ run }: { run: Run }) {
  const { t, dataHora } = useT()
  const ehMonteCarlo = run.kind === 'monte_carlo_when' || run.kind === 'monte_carlo_how_many'
  return (
    <div>
      <h2 className="ng-subtitulo">
        {run.name ?? t(`simulador.run.${run.kind}`)} <Tag>{t(`simulador.run.${run.kind}`)}</Tag>
      </h2>
      <p className="mini">{dataHora(run.created_at)}</p>
      {ehMonteCarlo ? (
        <MonteCarlo r={run.results as unknown as ResultadoMonteCarlo} />
      ) : run.kind === 'class_of_service_mix' ? (
        <MixDeClasses r={run.results as unknown as ResultadoExperimento} />
      ) : (
        <Experimento r={run.results as unknown as ResultadoExperimento} />
      )}
    </div>
  )
}

export function MonteCarlo({ r }: { r: ResultadoMonteCarlo }) {
  const { t, numero } = useT()
  const semanas = r.unit === 'weeks'
  const faixasDeValor = r.histogram.map((h) => ({
    de: h.x0 ?? h.x - 0.5,
    ate: h.x1 ?? h.x + 0.5,
    contagem: h.count,
  }))
  return (
    <div>
      <p className="ng-frase">
        <b>{r.sentence}</b>
      </p>
      {r.low_confidence && <Aviso tipo="alerta">{t('simulador.poucas_amostras', { n: r.samples?.length ?? 0 })}</Aviso>}
      <div className="ng-niveis">
        {r.levels.map((n) => (
          <div key={n.confidence} className={`ng-nivel${n.confidence === 85 ? ' recomendado' : ''}`}>
            <div className="ng-nivel-confianca">{n.confidence}%</div>
            <div className="ng-nivel-valor">
              {semanas ? t('simulador.semanas_valor', { n: n.weeks ?? 0 }) : t('simulador.itens_valor', { n: n.items ?? 0 })}
            </div>
            <div className="mini">{t('simulador.ate', { data: n.date_label })}</div>
            {n.confidence === 85 && <Tag cor="verde">{t('simulador.recomendado')}</Tag>}
          </div>
        ))}
      </div>
      <Histograma
        titulo={semanas ? t('simulador.hist_quando', { n: r.backlog_size ?? 0, tentativas: numero(r.trials, 0) }) : t('simulador.hist_quantos', { tentativas: numero(r.trials, 0) })}
        faixasDeValor={faixasDeValor}
        marcadores={r.levels.map((n) => ({
          rotulo: `P${n.confidence}`,
          valor: semanas ? (n.weeks ?? 0) : (n.items ?? 0),
          destaque: n.confidence === 85,
        }))}
        formatarX={(v) => numero(v, 0)}
        rotuloY={t('simulador.tentativas')}
        tabela={{ colunas: [semanas ? t('simulador.semanas_col') : t('simulador.itens_col'), t('simulador.tentativas')], linhas: r.histogram.map((h) => [h.label, h.count]) }}
      />
      {r.verdict && <div className="ng-veredito">{r.verdict}</div>}
    </div>
  )
}

function Experimento({ r }: { r: ResultadoExperimento }) {
  const { t, numero } = useT()
  const melhor = melhorLinha(r.rows)
  const n = (v: number | null | undefined, casas = 1) => (v == null ? '—' : numero(v, casas))
  return (
    <div>
      <div className="ng-duas">
        <Barras
          titulo={t('simulador.lead_p85_por_config')}
          nota={t('simulador.lead_p85_nota')}
          itens={r.rows.map((l) => ({ rotulo: l.name, valor: l.lead_time_p85 ?? 0 }))}
          formatarValor={(v) => numero(v, 1)}
          rotuloY={t('metricas.dias')}
        />
        <Barras
          titulo={t('simulador.wip_medio_por_config')}
          itens={r.rows.map((l) => ({ rotulo: l.name, valor: l.avg_wip ?? 0 }))}
          formatarValor={(v) => numero(v, 1)}
          cor="var(--gold)"
        />
      </div>
      <div className="tabela-rolagem">
        <table className="ng-tabela">
          <thead>
            <tr>
              <th>{t('simulador.config')}</th>
              <th>{t('simulador.lead_medio')}</th>
              <th>{t('simulador.lead_p85')}</th>
              <th>{t('simulador.vazao_semana')}</th>
              <th>{t('simulador.eficiencia')}</th>
              <th>{t('metricas.wip_medio')}</th>
              <th>{t('simulador.entregues')}</th>
            </tr>
          </thead>
          <tbody>
            {r.rows.map((l, i) => (
              <tr key={l.name} className={i === melhor ? 'ng-melhor' : undefined}>
                <td>
                  {l.name} {i === melhor && <Tag cor="verde">{t('simulador.melhor')}</Tag>}
                </td>
                <td>{n(l.lead_time_avg)}</td>
                <td>{n(l.lead_time_p85)}</td>
                <td>{n(vazaoDa(l))}</td>
                <td>{l.flow_efficiency_pct == null ? '—' : `${numero(l.flow_efficiency_pct, 0)}%`}</td>
                <td>{n(l.avg_wip)}</td>
                <td>{n(l.delivered, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.verdict && <div className="ng-veredito">{r.verdict}</div>}
    </div>
  )
}

function MixDeClasses({ r }: { r: ResultadoExperimento }) {
  const { t, numero } = useT()
  const n = (v: number | null | undefined, casas = 1) => (v == null ? '—' : numero(v, casas))
  return (
    <div>
      <div className="ng-duas">
        <Barras
          titulo={t('simulador.padrao_p85_por_urgente')}
          nota={t('simulador.padrao_p85_nota')}
          itens={r.rows.map((l) => ({ rotulo: `${numero(l.expedite_share_pct ?? 0, 0)}%`, valor: l.by_class?.standard?.lead_time_p85 ?? 0 }))}
          linha={{ rotulo: t('simulador.urgente_p85'), valores: r.rows.map((l) => l.expedite_lead_time_p85 ?? null) }}
          legendaBarras={t('simulador.padrao_p85')}
          formatarValor={(v) => numero(v, 1)}
          rotuloY={t('metricas.dias')}
        />
        <Barras
          titulo={t('simulador.vazao_por_urgente')}
          itens={r.rows.map((l) => ({ rotulo: `${numero(l.expedite_share_pct ?? 0, 0)}%`, valor: vazaoDa(l) ?? 0 }))}
          formatarValor={(v) => numero(v, 1)}
          cor="var(--gold)"
        />
      </div>
      <div className="tabela-rolagem">
        <table className="ng-tabela">
          <thead>
            <tr>
              <th>{t('simulador.share_urgente')}</th>
              <th>{t('simulador.padrao_p85')}</th>
              <th>{t('simulador.urgente_p85')}</th>
              <th>{t('simulador.geral_p85')}</th>
              <th>{t('simulador.vazao_semana')}</th>
            </tr>
          </thead>
          <tbody>
            {r.rows.map((l) => (
              <tr key={l.name}>
                <td>{l.name}</td>
                <td>{n(l.by_class?.standard?.lead_time_p85)}</td>
                <td>{n(l.expedite_lead_time_p85)}</td>
                <td>{n(l.overall_lead_time_p85)}</td>
                <td>{n(vazaoDa(l))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.verdict && <div className="ng-veredito">{r.verdict}</div>}
    </div>
  )
}
