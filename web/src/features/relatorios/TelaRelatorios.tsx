/**
 * A area de Reports do Nagare sozinho (onda 1, 24/11/2026; plano no cofre:
 * `produtos/nagare/planos/plano-area-de-reports.md`).
 *
 * - `/nagare/reports`: o catalogo dos relatorios que a pessoa pode abrir.
 * - `/nagare/reports/<modelo>?period=...`: o relatorio ao vivo, com o periodo e o escopo na URL
 *   (o link pode ser copiado), imprimir e baixar a planilha (Excel ou CSV).
 * - Onda 2 (25/11/2026): o botao Emitir guarda o relatorio congelado e abre o emitido
 *   (`/nagare/reports/issued/<id>`, em `TelaEmitidos.tsx`); o catalogo mostra os ultimos.
 *
 * As Metricas servem para explorar; os Reports, para prestar contas de um periodo fechado.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Aviso, Cabecalho, Carregando, ErroDeApi, Icone, hojeISO } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { consulta, nagare } from '../../api/nagare'
import { baixarLocal } from '../../api/sessaoLocal'
import { useProdutoSozinho } from '../../sozinho/produto'
import { mensagemDeErro, useT } from '../../i18n'
import { useProjetos, useQuadros, useVinculo } from '../quadro/consultas'
import { Documento } from './Documento'
import { corpoDaEmissao, type Emitido } from './emitidos'
import { UltimosEmitidos } from './TelaEmitidos'
import {
  buscaDosParametros,
  parametrosDaBusca,
  periodoVizinho,
  prontoParaPedir,
  proximoNoFuturo,
  type Documento as Doc,
  type ModeloDeRelatorio,
  type Parametros,
  type TipoDePeriodo,
} from './formato'

function useModelos() {
  const { produto } = useNagare()
  return useQuery({
    queryKey: ['nagare', produto, 'relatorios', 'modelos'],
    queryFn: () => nagare<{ items: ModeloDeRelatorio[] }>(produto, '/reports/templates'),
    staleTime: 60_000,
  })
}

export function TelaRelatorios() {
  const { modelo } = useParams()
  return modelo ? <TelaDoRelatorio key={modelo} modeloId={modelo} /> : <Catalogo />
}

function Catalogo() {
  const { t } = useT()
  const { rota } = useNagare()
  const modelos = useModelos()
  return (
    <div className="ng-relatorios">
      <Cabecalho kicker="Nagare" titulo={t('relatorios.titulo')} />
      <p className="ng-relatorios-descricao">{t('relatorios.descricao')}</p>
      {modelos.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : modelos.isError ? (
        <ErroDeApi erro={modelos.error} />
      ) : (
        <ul className="ng-relatorios-catalogo">
          {modelos.data.items.map((m) => (
            <li key={m.id} className={`card ng-relatorio-modelo${m.available ? '' : ' fechado'}`}>
              <div className="mini">{m.code}</div>
              <h3>{m.available ? <Link to={rota(`relatorios/${m.id}`)}>{m.title}</Link> : m.title}</h3>
              <p>{m.description}</p>
              {!m.available && <p className="mini">🔒 {t('relatorios.indisponivel')}</p>}
            </li>
          ))}
        </ul>
      )}
      <UltimosEmitidos />
    </div>
  )
}

function TelaDoRelatorio({ modeloId }: { modeloId: string }) {
  const { t } = useT()
  const { produto, rota } = useNagare()
  const navegar = useNavigate()
  const [busca, setBusca] = useSearchParams()
  const modelos = useModelos()
  const quadros = useQuadros()
  const projetos = useProjetos()
  const vinculo = useVinculo()
  const { trocar } = useProdutoSozinho()
  const modelo = modelos.data?.items.find((m) => m.id === modeloId)
  const p = useMemo(() => parametrosDaBusca(busca), [busca])

  const mudar = (novo: Partial<Parametros>) => {
    setBusca(new URLSearchParams(buscaDosParametros({ ...p, ...novo }).slice(1)), { replace: true })
  }

  // Quadro padrao, como nas Metricas: o do produto, senao o primeiro ativo.
  const quadroPadrao = vinculo.data?.link?.board_id ?? quadros.data?.items.find((q) => !q.archived)?.id
  useEffect(() => {
    if (modelo?.scope === 'board' && !p.board_id && quadroPadrao) mudar({ board_id: quadroPadrao })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelo?.scope, p.board_id, quadroPadrao])

  const pronto = modelo ? prontoParaPedir(modelo, p) : false
  const caminho = `/reports/${encodeURIComponent(modeloId)}${consulta({ ...p })}`
  const relatorio = useQuery({
    queryKey: ['nagare', produto, 'relatorios', modeloId, caminho],
    queryFn: () => nagare<Doc>(produto, caminho),
    enabled: pronto && Boolean(modelo?.available),
    placeholderData: (anterior) => anterior,
  })

  if (modelos.isPending) return <Carregando texto={t('comum.carregando')} />
  if (modelos.isError) return <ErroDeApi erro={modelos.error} />
  if (!modelo) return <Aviso tipo="erro">{t('erro.not_found')}</Aviso>

  const abrirProduto = (outro: string, quadroId: string) => {
    if (outro !== produto) trocar(outro)
    navegar(`${rota('relatorios/delivery_review')}${buscaDosParametros({ period: p.period, date: p.date, from: p.from, to: p.to, board_id: quadroId })}`)
  }
  const doc = relatorio.data
  const hoje = hojeISO()

  return (
    <div className="ng-relatorios">
      <div className="ng-relatorio-controles">
        <Link className="ng-relatorio-voltar mini" to={rota('relatorios')}>
          ‹ {t('relatorios.voltar')}
        </Link>
        <Cabecalho
          kicker={t('relatorios.titulo')}
          titulo={modelo.title}
          acoes={<AcoesDoRelatorio modeloId={modeloId} p={p} habilitado={Boolean(doc) && pronto} />}
        />
        <div className="ng-barra ng-relatorio-barra">
          {modelo.scope === 'board' && (
            <select aria-label={t('relatorios.quadro')} value={p.board_id ?? ''} onChange={(e) => mudar({ board_id: e.target.value })}>
              <option value="" disabled>
                {t('relatorios.escolha_quadro')}
              </option>
              {(quadros.data?.items ?? [])
                .filter((q) => !q.archived)
                .map((q) => (
                  <option key={q.id} value={q.id}>
                    {q.name}
                  </option>
                ))}
            </select>
          )}
          {(modelo.scope === 'project' || modelo.scope === 'projects') && (
            <select
              aria-label={modelo.scope === 'project' ? t('relatorios.projeto') : t('relatorios.projetos')}
              value={(modelo.scope === 'project' ? p.project_id : p.project_ids) ?? ''}
              onChange={(e) => mudar(modelo.scope === 'project' ? { project_id: e.target.value } : { project_ids: e.target.value })}
            >
              <option value="" disabled={modelo.scope === 'project'}>
                {modelo.scope === 'project' ? t('relatorios.escolha_projeto') : t('relatorios.todos_projetos')}
              </option>
              {(projetos.data?.items ?? [])
                .filter((pr) => !pr.archived)
                .map((pr) => (
                  <option key={pr.id} value={pr.id}>
                    {pr.key} · {pr.name}
                  </option>
                ))}
            </select>
          )}
          <div className="abas" role="tablist" aria-label={t('relatorios.periodo')}>
            {(['week', 'month', 'custom'] as TipoDePeriodo[]).map((tipo) => (
              <button
                key={tipo}
                type="button"
                role="tab"
                aria-selected={p.period === tipo}
                onClick={() =>
                  mudar(
                    tipo === 'custom'
                      ? { period: tipo, date: undefined, from: doc?.period.from ?? p.from, to: doc?.period.to ?? p.to }
                      : { period: tipo, from: undefined, to: undefined, date: doc?.period.from },
                  )
                }
              >
                {t(tipo === 'week' ? 'relatorios.semana' : tipo === 'month' ? 'relatorios.mes' : 'relatorios.intervalo')}
              </button>
            ))}
          </div>
          {p.period === 'custom' ? (
            <>
              <label className="ng-relatorio-data">
                {t('relatorios.de')}
                <input type="date" value={p.from ?? ''} max={hoje} onChange={(e) => mudar({ from: e.target.value })} />
              </label>
              <label className="ng-relatorio-data">
                {t('relatorios.ate')}
                <input type="date" value={p.to ?? ''} max={hoje} onChange={(e) => mudar({ to: e.target.value })} />
              </label>
            </>
          ) : (
            doc && (
              <div className="ng-relatorio-navegar">
                <button type="button" className="btn sm ghost" aria-label={t('relatorios.anterior')} title={t('relatorios.anterior')} onClick={() => mudar(periodoVizinho(doc, -1))}>
                  ‹
                </button>
                <span>{doc.period.label}</span>
                <button
                  type="button"
                  className="btn sm ghost"
                  aria-label={t('relatorios.proximo')}
                  title={t('relatorios.proximo')}
                  disabled={proximoNoFuturo(doc, hoje)}
                  onClick={() => mudar(periodoVizinho(doc, 1))}
                >
                  ›
                </button>
              </div>
            )
          )}
        </div>
      </div>

      {!pronto ? (
        <Aviso tipo="info">{modelo.scope === 'project' ? t('relatorios.escolha_projeto') : t('relatorios.escolha_quadro')}</Aviso>
      ) : relatorio.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : relatorio.isError ? (
        <ErroDeApi erro={relatorio.error} />
      ) : (
        <div className={relatorio.isFetching ? 'ng-relatorio-ocupado' : undefined}>
          <Documento doc={relatorio.data} abrirProduto={modeloId === 'portfolio' ? abrirProduto : undefined} />
        </div>
      )}
    </div>
  )
}

function AcoesDoRelatorio({ modeloId, p, habilitado }: { modeloId: string; p: Parametros; habilitado: boolean }) {
  const { t, eu } = useT()
  const { produto, rota } = useNagare()
  const navegar = useNavigate()
  const queryClient = useQueryClient()
  const [baixando, setBaixando] = useState(false)
  const [erro, setErro] = useState(false)
  const podeEmitir = Boolean(eu?.capabilities.issue_reports)
  const emitir = useMutation({
    mutationFn: () => nagare<Emitido>(produto, '/reports/issued', { metodo: 'POST', corpo: corpoDaEmissao(modeloId, p) }),
    onSuccess: (emitido) => {
      void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'emitidos'] })
      queryClient.setQueryData(['nagare', produto, 'emitidos', emitido.id], emitido)
      navegar(rota(`relatorios/emitidos/${emitido.id}`))
    },
  })

  const baixar = async (formato: 'xlsx' | 'csv') => {
    setBaixando(true)
    setErro(false)
    try {
      const { arquivo, nome } = await baixarLocal(`/reports/${encodeURIComponent(modeloId)}/export${consulta({ ...p, format: formato })}`, {
        'X-Produto': produto,
      })
      const url = URL.createObjectURL(arquivo)
      const a = document.createElement('a')
      a.href = url
      a.download = nome
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch {
      setErro(true)
    } finally {
      setBaixando(false)
    }
  }

  return (
    <div className="ng-relatorio-acoes">
      <button type="button" className="btn sm" disabled={!habilitado} title={t('relatorios.dica_imprimir')} onClick={() => window.print()}>
        <Icone nome="documento" tamanho={16} /> {t('relatorios.imprimir')}
      </button>
      <button type="button" className="btn sm ghost" disabled={!habilitado || baixando} onClick={() => baixar('xlsx')}>
        {baixando ? t('relatorios.baixando') : t('relatorios.excel')}
      </button>
      <button type="button" className="btn sm ghost" disabled={!habilitado || baixando} onClick={() => baixar('csv')}>
        {t('relatorios.csv')}
      </button>
      {podeEmitir && (
        <button type="button" className="btn sm" disabled={!habilitado || emitir.isPending} title={t('relatorios.dica_emitir')} onClick={() => emitir.mutate()}>
          {emitir.isPending ? t('relatorios.emitindo') : t('relatorios.emitir')}
        </button>
      )}
      {emitir.isError && (
        <span className="ng-ruim mini" role="alert">
          {mensagemDeErro(emitir.error, t)}
        </span>
      )}
      {erro && (
        <span className="ng-ruim mini" role="alert">
          {t('relatorios.erro_planilha')}
        </span>
      )}
    </div>
  )
}
