/**
 * Previsao Monte Carlo nas metricas (decisao de 18/10/2026): as duas perguntas do simulador,
 * "quando termino N itens?" e "quantos cabem ate a data?", pela vazao semanal do proprio quadro
 * (`GET /boards/{id}/metrics/forecast`). So leitura: nao grava execucao, vale para quem le as
 * metricas e respeita a flag `monte_carlo`. Os mesmos padroes do simulador (40 itens, daqui a 60
 * dias), para as duas telas darem a mesma resposta.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Carregando, ErroDeApi, hojeISO } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { consulta, nagare } from '../../api/nagare'
import { useT } from '../../i18n'
import { MonteCarlo } from '../simulador/ResultadoDoRun'
import { emDias, type ResultadoMonteCarlo } from '../simulador/tipos'
import { Bloqueado } from './TelaMetricas'

type Pergunta = 'when' | 'how_many'

interface Pedido {
  pergunta: Pergunta
  backlog: number
  dataAlvo: string
}

export function PainelPrevisao({ quadroId }: { quadroId: string }) {
  const { t, eu } = useT()
  const { produto } = useNagare()
  const hoje = hojeISO()
  const daqui60 = new Date(`${hoje}T00:00:00Z`)
  daqui60.setUTCDate(daqui60.getUTCDate() + 60)
  const [rascunho, setRascunho] = useState<Pedido>({ pergunta: 'when', backlog: 40, dataAlvo: daqui60.toISOString().slice(0, 10) })
  const [pedido, setPedido] = useState<Pedido>(rascunho)
  const ligado = eu?.flags.monte_carlo !== false
  const previsao = useQuery({
    queryKey: ['nagare', produto, 'previsao', quadroId, pedido],
    queryFn: () =>
      nagare<ResultadoMonteCarlo>(
        produto,
        `/boards/${quadroId}/metrics/forecast${consulta(
          pedido.pergunta === 'when'
            ? { question: 'when', backlog_size: pedido.backlog }
            : { question: 'how_many', days: emDias(pedido.dataAlvo, hoje) },
        )}`,
      ),
    enabled: ligado,
    staleTime: 60_000,
  })

  return (
    <section className={`card ng-painel largo ng-previsao${previsao.isFetching ? ' ocupado' : ''}`} aria-label={t('previsao.titulo')}>
      <div className="ng-figura-topo">
        <h3>{t('previsao.titulo')}</h3>
      </div>
      <p className="mini ng-figura-nota">{t('previsao.nota')}</p>
      {!ligado ? (
        <Bloqueado flag="monte_carlo" />
      ) : (
        <>
          <form
            className="ng-linha-campos ng-previsao-pedido"
            onSubmit={(e) => {
              e.preventDefault()
              setPedido(rascunho)
            }}
          >
            <div className="abas" role="radiogroup" aria-label={t('previsao.pergunta')}>
              {(['when', 'how_many'] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  role="radio"
                  aria-checked={rascunho.pergunta === p}
                  aria-selected={rascunho.pergunta === p}
                  onClick={() => setRascunho({ ...rascunho, pergunta: p })}
                >
                  {t(p === 'when' ? 'simulador.quando' : 'simulador.quantos')}
                </button>
              ))}
            </div>
            {rascunho.pergunta === 'when' ? (
              <label className="campo ng-campo-curto">
                <span>{t('simulador.backlog')}</span>
                <input
                  type="number"
                  min={1}
                  value={rascunho.backlog}
                  onChange={(e) => setRascunho({ ...rascunho, backlog: Math.max(1, Number(e.target.value) || 1) })}
                />
              </label>
            ) : (
              <label className="campo ng-campo-curto">
                <span>{t('simulador.data_alvo')}</span>
                <input type="date" min={hoje} value={rascunho.dataAlvo} onChange={(e) => setRascunho({ ...rascunho, dataAlvo: e.target.value })} />
              </label>
            )}
            <button type="submit" className="btn sm ng-alinhar-baixo">
              {t('previsao.prever')}
            </button>
          </form>
          {previsao.isPending ? (
            <Carregando texto={t('comum.carregando')} />
          ) : previsao.isError ? (
            <ErroDeApi erro={previsao.error} />
          ) : (
            <MonteCarlo r={previsao.data} />
          )}
        </>
      )}
    </section>
  )
}
