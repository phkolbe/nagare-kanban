/**
 * Visao consolidada da PO (`/grupo-sr/pampufa/tarefas`, decisao de 17/10/2026): os quadros dos
 * produtos lidos por etapa, uma raia por produto, so leitura e sem cartao repetido (NG4).
 * O cartao abre no quadro do proprio produto.
 */

import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Cabecalho, Carregando, ErroDeApi, MarcaProduto, Vazio, useEu, useProduto } from '@pampufa/ui'

import { chaves, nagare } from '../../api/nagare'
import type { Consolidado } from '../../api/tipos'
import { SeletorIdioma } from '../../componentes/SeletorIdioma'
import { useT } from '../../i18n'

export function TelaConsolidado() {
  const { t, data } = useT()
  const eu = useEu()
  const produto = useProduto().id
  const consolidado = useQuery({
    queryKey: chaves.consolidado(),
    queryFn: () => nagare<Consolidado>(produto, '/integration/overview'),
    refetchInterval: 120_000,
  })
  const nome = (id: string) => eu.produtos.find((p) => p.id === id)?.nome ?? id

  return (
    <>
      <Cabecalho kicker={t('consolidado.kicker')} titulo={t('consolidado.titulo')} descricao={t('consolidado.descricao')} acoes={<SeletorIdioma />} />
      {consolidado.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : consolidado.isError ? (
        <ErroDeApi erro={consolidado.error} />
      ) : consolidado.data.products.length === 0 ? (
        <section className="card">
          <Vazio titulo={t('consolidado.vazio')} />
        </section>
      ) : (
        <div className="ng-grade-rolagem">
          <div className="ng-consolidado" style={{ gridTemplateColumns: `180px repeat(${consolidado.data.stages.length}, minmax(200px, 1fr))` }}>
            <div className="ng-consolidado-canto" />
            {consolidado.data.stages.map((etapa) => (
              <div key={etapa} className="ng-cabecalho ng-etapa">
                <b>{t(`etapa.${etapa}`)}</b>
                <span className="mini">
                  {etapa === 'entregue'
                    ? t('consolidado.ultimos_dias', { n: consolidado.data.delivered_window_days })
                    : t(`etapa.${etapa}.dica`)}
                </span>
              </div>
            ))}
            {consolidado.data.products.map((raia) => [
              <div key={`${raia.product_id}-nome`} className="ng-raia-produto">
                <MarcaProduto produto={raia.product_id} tamanho={24} />
                <div>
                  <b>{nome(raia.product_id)}</b>
                  <div className="mini">{raia.board.name}</div>
                </div>
              </div>,
              ...consolidado.data.stages.map((etapa) => {
                const cartoes = raia.cards.filter((c) => c.stage === etapa)
                const limite = raia.wip[etapa]
                const cheio = limite != null && cartoes.length >= limite
                return (
                  <div key={`${raia.product_id}-${etapa}`} className="ng-celula ng-celula-consolidada">
                    <div className={`ng-contagem${cheio ? ' cheio' : ''}`}>
                      {cartoes.length}
                      {limite != null && <span className="mini">/{limite}</span>}
                    </div>
                    {cartoes.slice(0, 12).map((c) => (
                      <Link
                        key={c.id}
                        className={`ng-cartao ng-cartao-compacto classe-${c.service_class}`}
                        to={`/${raia.product_id}/tarefas/quadro/${raia.board.id}?cartao=${c.id}`}
                      >
                        <span className="ng-chave">{c.key}</span>
                        {c.is_blocked && <span className="ng-sinal bloqueado">⛔</span>}
                        <span className="ng-titulo">{c.sealed ? t('cartao.lacrado') : c.title}</span>
                        <span className="mini">
                          {c.assignee_name ?? t('cartao.sem_responsavel')}
                          {c.due_date && ` · ${data(c.due_date)}`}
                        </span>
                      </Link>
                    ))}
                    {cartoes.length > 12 && (
                      <Link className="link mini" to={`/${raia.product_id}/tarefas/quadro/${raia.board.id}`}>
                        {t('consolidado.mais', { n: cartoes.length - 12 })}
                      </Link>
                    )}
                  </div>
                )
              }),
            ])}
          </div>
        </div>
      )}
    </>
  )
}
