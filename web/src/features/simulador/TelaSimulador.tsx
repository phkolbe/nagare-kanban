/**
 * Simulador (paridade com a tela antiga, N3): quadros de simulacao isolados (clonar a estrutura
 * de um quadro real, ou em branco), gerar demanda por semente, previsao Monte Carlo (quando? /
 * quantos?), experimentos de WIP, mix de classes e replay, historico e comparacao de runs.
 * Nada aqui mexe nas metricas operacionais.
 */

import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Aviso, Cabecalho, Carregando, ErroDeApi, Tag, Vazio, hojeISO } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { nagare } from '../../api/nagare'
import { CLASSES, type ClasseServico, type Lista, type Quadro } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { Janela } from '../../componentes/Sobreposicao'
import { SeletorIdioma } from '../../componentes/SeletorIdioma'
import { mensagemDeErro, useT } from '../../i18n'
import { Bloqueado } from '../metricas/TelaMetricas'
import { useQuadros } from '../quadro/consultas'
import { ordenarColunas } from '../quadro/regras'
import { Comparar } from './Comparar'
import { ResultadoDoRun } from './ResultadoDoRun'
import { rebalancear, type Run, type TipoRun } from './tipos'

const SEMENTE = 20260816

export function TelaSimulador() {
  const { quadroId } = useParams()
  const { t, eu } = useT()
  if (eu?.flags.simulator === false) {
    return (
      <>
        <Cabecalho kicker={t('quadro.kicker')} titulo={t('simulador.titulo')} acoes={<SeletorIdioma />} />
        <section className="card">
          <Bloqueado flag="simulator" />
        </section>
      </>
    )
  }
  if (eu && !eu.capabilities.simulator_boards) {
    return <Aviso tipo="alerta">{t('simulador.sem_papel')}</Aviso>
  }
  return quadroId ? <Bancada key={quadroId} quadroId={quadroId} /> : <IndiceDoSimulador />
}

function useQuadrosDeSimulacao() {
  const { produto } = useNagare()
  return useQuery({
    queryKey: ['nagare', produto, 'simulador', 'quadros'],
    queryFn: () => nagare<Lista<Quadro & { source_board_id: string | null; created_at: string | null }>>(produto, '/simulator/boards'),
  })
}

function IndiceDoSimulador() {
  const { t, dataHora } = useT()
  const { rota } = useNagare()
  const quadros = useQuadrosDeSimulacao()
  const operacionais = useQuadros()
  const [novo, setNovo] = useState(false)
  const nomeDe = (id: string | null) => operacionais.data?.items.find((q) => q.id === id)?.name
  return (
    <>
      <Cabecalho
        kicker={t('quadro.kicker')}
        titulo={t('simulador.titulo')}
        descricao={t('simulador.descricao')}
        acoes={
          <>
            <button type="button" className="btn sm" onClick={() => setNovo(true)}>
              {t('simulador.novo')}
            </button>
            <SeletorIdioma />
          </>
        }
      />
      <Aviso tipo="info">{t('simulador.caixa_de_areia')}</Aviso>
      {quadros.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : quadros.isError ? (
        <ErroDeApi erro={quadros.error} />
      ) : quadros.data.items.length === 0 ? (
        <section className="card">
          <Vazio titulo={t('simulador.nenhum')} />
        </section>
      ) : (
        <div className="ng-projetos">
          {quadros.data.items.map((q) => (
            <article key={q.id} className="card ng-projeto-cartao">
              <div className="ng-projeto-topo">
                <Tag cor="ouro">{t('simulador.tag')}</Tag>
                {q.archived && <Tag>{t('cartao.arquivado')}</Tag>}
              </div>
              <h3>
                <Link to={rota(`simulador/${q.id}`)}>{q.name}</Link>
              </h3>
              <p className="mini">{t('cartao.criado_em', { quando: dataHora(q.created_at) })}</p>
              {q.source_board_id && <p className="mini">{t('simulador.clonado_de', { quadro: nomeDe(q.source_board_id) ?? '—' })}</p>}
            </article>
          ))}
        </div>
      )}
      {novo && <NovoQuadroDeSimulacao operacionais={operacionais.data?.items ?? []} fechar={() => setNovo(false)} />}
    </>
  )
}

function NovoQuadroDeSimulacao({ operacionais, fechar }: { operacionais: Quadro[]; fechar: () => void }) {
  const { t } = useT()
  const { produto, rota } = useNagare()
  const navegar = useNavigate()
  const queryClient = useQueryClient()
  const [nome, setNome] = useState(t('simulador.nome_padrao'))
  // Origem: o id de um quadro para clonar, ou um modelo (`blank` corrige o defeito da tela antiga).
  const [origem, setOrigem] = useState(operacionais.find((q) => !q.read_only)?.id ?? 'blank')
  const criar = useMutation({
    mutationFn: () =>
      nagare<Quadro>(produto, '/simulator/boards', {
        metodo: 'POST',
        corpo: origem === 'blank' || origem === 'default' ? { name: nome.trim(), template: origem } : { name: nome.trim(), source_board_id: origem },
      }),
    onSuccess: (criado) => {
      void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'simulador'] })
      navegar(rota(`simulador/${criado.id}`))
    },
  })
  return (
    <Janela
      titulo={t('simulador.novo')}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" type="button" disabled={!nome.trim() || criar.isPending} onClick={() => criar.mutate()}>
            {t('comum.criar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <label className="campo">
        <span>{t('estrutura.nome_quadro')}</span>
        <input value={nome} maxLength={120} autoFocus onChange={(e) => setNome(e.target.value)} />
      </label>
      <label className="campo">
        <span>{t('simulador.origem')}</span>
        <select value={origem} onChange={(e) => setOrigem(e.target.value)}>
          {operacionais
            .filter((q) => !q.archived)
            .map((q) => (
              <option key={q.id} value={q.id}>
                {t('simulador.clonar', { quadro: q.name })}
              </option>
            ))}
          <option value="default">{t('simulador.modelo_padrao')}</option>
          <option value="blank">{t('simulador.em_branco')}</option>
        </select>
      </label>
      {criar.isError && <Aviso tipo="erro">{mensagemDeErro(criar.error, t)}</Aviso>}
    </Janela>
  )
}

interface Cenario {
  semanas: number
  chegadas: number
  mix: Record<ClasseServico, number>
  wip: Record<string, string>
  semente: number
  tentativas: number
  backlog: number
  dataAlvo: string
  pergunta: 'monte_carlo_when' | 'monte_carlo_how_many'
}

function emDias(dataAlvo: string, hoje: string): number {
  return Math.max(1, Math.round((Date.parse(`${dataAlvo}T00:00:00Z`) - Date.parse(`${hoje}T00:00:00Z`)) / 86_400_000))
}

function Bancada({ quadroId }: { quadroId: string }) {
  const { t, eu, numero, dataHora } = useT()
  const { produto, rota } = useNagare()
  const queryClient = useQueryClient()
  const avisar = useAvisos()
  const hoje = hojeISO()
  const quadro = useQuery({
    queryKey: ['nagare', produto, 'quadro', quadroId],
    queryFn: () => nagare<Quadro>(produto, `/boards/${encodeURIComponent(quadroId)}`),
  })
  const runs = useQuery({
    queryKey: ['nagare', produto, 'simulador', 'runs', quadroId],
    queryFn: () => nagare<Lista<Run>>(produto, `/simulator/runs?board_id=${encodeURIComponent(quadroId)}`),
  })
  const daqui60 = new Date(`${hoje}T00:00:00Z`)
  daqui60.setUTCDate(daqui60.getUTCDate() + 60)
  const [c, setC] = useState<Cenario>({
    semanas: 12,
    chegadas: 8,
    mix: { expedite: 8, standard: 70, fixed_date: 12, intangible: 10 },
    wip: {},
    semente: SEMENTE,
    tentativas: 10_000,
    backlog: 40,
    dataAlvo: daqui60.toISOString().slice(0, 10),
    pergunta: 'monte_carlo_when',
  })
  const [aberto, setAberto] = useState<string | null>(null)
  const [marcados, setMarcados] = useState<string[]>([])
  const [comparando, setComparando] = useState(false)
  const [confirmar, setConfirmar] = useState<'zerar' | 'gerar' | null>(null)

  const parametros = () => ({
    weeks: c.semanas,
    arrival_rate: c.chegadas,
    class_mix: Object.fromEntries(CLASSES.map((k) => [k, c.mix[k] / 100])),
    seed: c.semente,
    wip_limits: Object.fromEntries(Object.entries(c.wip).filter(([, v]) => v !== '').map(([k, v]) => [k, Number(v)])),
  })
  const recarregar = () => {
    void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'simulador'] })
    void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'visao', quadroId] })
  }
  const rodar = useMutation({
    mutationFn: (tipo: TipoRun) => {
      const extra: Record<string, unknown> =
        tipo === 'monte_carlo_when'
          ? { backlog_size: c.backlog, trials: c.tentativas }
          : tipo === 'monte_carlo_how_many'
            ? { days: emDias(c.dataAlvo, hoje), trials: c.tentativas }
            : {}
      avisar(t('simulador.rodando'), 'info')
      return nagare<Run>(produto, '/simulator/runs', { metodo: 'POST', corpo: { board_id: quadroId, kind: tipo, params: { ...parametros(), ...extra } } })
    },
    onSuccess: (run) => {
      avisar(t('simulador.pronto'), 'ok')
      setAberto(run.id)
      recarregar()
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })
  const gerar = useMutation({
    mutationFn: () =>
      nagare<{ cards_created: number }>(produto, `/simulator/boards/${quadroId}/generate`, { metodo: 'POST', corpo: { ...parametros(), reset: true } }),
    onSuccess: (r) => {
      avisar(t('simulador.gerado', { n: r.cards_created }), 'ok')
      recarregar()
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })
  const zerar = useMutation({
    mutationFn: () => nagare(produto, `/simulator/boards/${quadroId}/reset`, { metodo: 'POST' }),
    onSuccess: () => {
      avisar(t('simulador.zerado'), 'ok')
      recarregar()
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })

  if (quadro.isPending) return <Carregando texto={t('comum.carregando')} />
  if (quadro.isError) return <ErroDeApi erro={quadro.error} />
  const q = quadro.data
  if (q.kind !== 'simulator') {
    return (
      <section className="card">
        <Vazio titulo={t('simulador.nao_e_simulacao')}>
          <Link className="btn sm" to={rota('simulador')}>
            {t('simulador.titulo')}
          </Link>
        </Vazio>
      </section>
    )
  }
  const colunasWip = ordenarColunas(q.columns ?? []).filter((col) => col.kind !== 'done' && col.kind !== 'envelope')
  const runAberto = runs.data?.items.find((r) => r.id === aberto)
  const monteCarlo = eu?.flags.monte_carlo !== false
  const somaMix = CLASSES.reduce((s, k) => s + c.mix[k], 0)

  return (
    <>
      <Cabecalho
        kicker={t('simulador.titulo')}
        titulo={q.name}
        descricao={q.description ?? undefined}
        acoes={
          <>
            <Link className="btn sm sec" to={rota(`quadro/${q.id}`)}>
              {t('simulador.ver_quadro')}
            </Link>
            <button type="button" className="btn sm ghost" onClick={() => setConfirmar('zerar')}>
              {t('simulador.zerar')}
            </button>
            <SeletorIdioma />
          </>
        }
      />
      <Aviso tipo="info">{t('simulador.caixa_de_areia')}</Aviso>

      <section className="card">
        <h2 className="ng-subtitulo">{t('simulador.cenario')}</h2>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('simulador.semanas', { n: c.semanas })}</span>
            <input type="range" min={2} max={52} value={c.semanas} onChange={(e) => setC({ ...c, semanas: Number(e.target.value) })} />
          </label>
          <label className="campo">
            <span>{t('simulador.chegadas', { n: numero(c.chegadas, 1) })}</span>
            <input type="range" min={1} max={50} step={0.5} value={c.chegadas} onChange={(e) => setC({ ...c, chegadas: Number(e.target.value) })} />
          </label>
        </div>
        <fieldset className="ng-secao">
          <legend>{t('simulador.mix', { total: somaMix })}</legend>
          <div className="ng-mix-barra" aria-hidden="true">
            {CLASSES.map((k) => (
              <span key={k} className={`classe-${k}`} style={{ width: `${c.mix[k]}%` }} />
            ))}
          </div>
          <div className="ng-linha-campos">
            {CLASSES.map((k) => (
              <label key={k} className="campo">
                <span>
                  {t(`classe.${k}`)}: {c.mix[k]}%
                </span>
                <input type="range" min={0} max={100} value={c.mix[k]} onChange={(e) => setC({ ...c, mix: rebalancear(c.mix, k, Number(e.target.value)) })} />
              </label>
            ))}
          </div>
        </fieldset>
        {colunasWip.length > 0 && (
          <fieldset className="ng-secao">
            <legend>{t('simulador.wip_por_coluna')}</legend>
            <div className="ng-linha-campos">
              {colunasWip.map((col) => (
                <label key={col.id} className="campo ng-campo-curto">
                  <span>{col.name}</span>
                  <input
                    type="number"
                    min={0}
                    placeholder={col.wip_max?.toString() ?? '∞'}
                    value={c.wip[col.id] ?? ''}
                    onChange={(e) => setC({ ...c, wip: { ...c.wip, [col.id]: e.target.value } })}
                  />
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <div className="ng-linha-campos">
          <label className="campo ng-campo-curto">
            <span>{t('simulador.semente')}</span>
            <input type="number" value={c.semente} onChange={(e) => setC({ ...c, semente: Number(e.target.value) || SEMENTE })} />
            <small className="mini">{t('simulador.semente_dica')}</small>
          </label>
          <label className="campo ng-campo-curto">
            <span>{t('simulador.tentativas')}</span>
            <input type="number" min={100} step={100} value={c.tentativas} onChange={(e) => setC({ ...c, tentativas: Math.max(100, Number(e.target.value) || 10_000) })} />
            <small className="mini">{t('simulador.so_monte_carlo')}</small>
          </label>
        </div>
        <p className="mini">
          {t('simulador.resumo', { semanas: c.semanas, chegadas: numero(c.chegadas, 1), semente: c.semente, tentativas: numero(c.tentativas, 0) })}
        </p>
        <div className="acoes">
          <button type="button" className="btn sm sec" disabled={gerar.isPending} onClick={() => setConfirmar('gerar')}>
            {t('simulador.gerar')}
          </button>
        </div>
      </section>

      <section className="card">
        <h2 className="ng-subtitulo">{t('simulador.previsao')}</h2>
        {!monteCarlo && <p className="mini">{t('simulador.precisa_monte_carlo')}</p>}
        <div className="ng-linha-campos">
          <label className="ng-alternar" title={monteCarlo ? undefined : t('simulador.precisa_monte_carlo')}>
            <input type="radio" name="pergunta" disabled={!monteCarlo} checked={c.pergunta === 'monte_carlo_when'} onChange={() => setC({ ...c, pergunta: 'monte_carlo_when' })} />
            {t('simulador.quando')}
          </label>
          <label className="ng-alternar" title={monteCarlo ? undefined : t('simulador.precisa_monte_carlo')}>
            <input type="radio" name="pergunta" disabled={!monteCarlo} checked={c.pergunta === 'monte_carlo_how_many'} onChange={() => setC({ ...c, pergunta: 'monte_carlo_how_many' })} />
            {t('simulador.quantos')}
          </label>
        </div>
        {c.pergunta === 'monte_carlo_when' ? (
          <label className="campo ng-campo-curto">
            <span>{t('simulador.backlog')}</span>
            <input type="number" min={1} value={c.backlog} disabled={!monteCarlo} onChange={(e) => setC({ ...c, backlog: Math.max(1, Number(e.target.value) || 1) })} />
          </label>
        ) : (
          <label className="campo ng-campo-curto">
            <span>{t('simulador.data_alvo')}</span>
            <input type="date" min={hoje} value={c.dataAlvo} disabled={!monteCarlo} onChange={(e) => setC({ ...c, dataAlvo: e.target.value })} />
          </label>
        )}
        <div className="acoes">
          <button type="button" className="btn sm" disabled={!monteCarlo || rodar.isPending} onClick={() => rodar.mutate(c.pergunta)}>
            {t('simulador.rodar_previsao')}
          </button>
        </div>
        <h2 className="ng-subtitulo">{t('simulador.experimentos')}</h2>
        <div className="acoes">
          <button type="button" className="btn sm sec" disabled={rodar.isPending} onClick={() => rodar.mutate('wip_experiment')}>
            {t('simulador.run.wip_experiment')}
          </button>
          <button type="button" className="btn sm sec" disabled={rodar.isPending} onClick={() => rodar.mutate('class_of_service_mix')}>
            {t('simulador.run.class_of_service_mix')}
          </button>
          <button type="button" className="btn sm sec" disabled={rodar.isPending} onClick={() => rodar.mutate('replay')}>
            {t('simulador.run.replay')}
          </button>
        </div>
      </section>

      {runAberto && (
        <section className="card">
          <ResultadoDoRun run={runAberto} />
        </section>
      )}

      <section className="card">
        <h2 className="ng-subtitulo">{t('simulador.historico')}</h2>
        {runs.isPending ? (
          <Carregando texto={t('comum.carregando')} />
        ) : runs.isError ? (
          <ErroDeApi erro={runs.error} />
        ) : runs.data.items.length === 0 ? (
          <p className="mini">{t('simulador.sem_runs')}</p>
        ) : (
          <ul className="ng-runs">
            {runs.data.items.map((r) => (
              <li key={r.id} className={aberto === r.id ? 'aberto' : undefined}>
                <label className="ng-alternar">
                  <input
                    type="checkbox"
                    aria-label={t('simulador.marcar', { nome: r.name ?? r.kind })}
                    checked={marcados.includes(r.id)}
                    onChange={(e) => setMarcados(e.target.checked ? [...marcados, r.id] : marcados.filter((x) => x !== r.id))}
                  />
                </label>
                <button type="button" className="link" onClick={() => setAberto(r.id)}>
                  {r.name ?? t(`simulador.run.${r.kind}`)}
                </button>
                <Tag>{t(`simulador.run.${r.kind}`)}</Tag>
                <span className="mini">
                  {dataHora(r.created_at)}
                  {r.created_by_name && ` · ${r.created_by_name}`}
                </span>
              </li>
            ))}
          </ul>
        )}
        {marcados.length >= 2 && (
          <div className="ng-barra-comparar">
            <span>{t('simulador.marcados', { n: marcados.length })}</span>
            <button type="button" className="btn sm" onClick={() => setComparando(true)}>
              {t('simulador.comparar')}
            </button>
          </div>
        )}
      </section>

      {comparando && <Comparar ids={marcados} runs={runs.data?.items ?? []} fechar={() => setComparando(false)} />}
      {confirmar && (
        <Janela
          titulo={t(confirmar === 'zerar' ? 'simulador.zerar' : 'simulador.gerar')}
          fechar={() => setConfirmar(null)}
          largura={480}
          rodape={
            <>
              <button
                className="btn"
                type="button"
                onClick={() => {
                  if (confirmar === 'zerar') zerar.mutate()
                  else gerar.mutate()
                  setConfirmar(null)
                }}
              >
                {t('comum.confirmar')}
              </button>
              <button className="btn ghost" type="button" autoFocus onClick={() => setConfirmar(null)}>
                {t('comum.cancelar')}
              </button>
            </>
          }
        >
          <p>
            {confirmar === 'zerar'
              ? t('simulador.zerar_texto', { quadro: q.name })
              : t('simulador.gerar_texto', { semanas: c.semanas, cartoes: Math.round(c.semanas * c.chegadas), quadro: q.name })}
          </p>
        </Janela>
      )}
    </>
  )
}
