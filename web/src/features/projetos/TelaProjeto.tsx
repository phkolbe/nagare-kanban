/** Um projeto: envelopes lacrados, em descoberta, em andamento e entregues; nova demanda. */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Cabecalho, Carregando, ErroDeApi, Tag, Vazio } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import type { Cartao, ProjetoDetalhe } from '../../api/tipos'
import { SeletorIdioma } from '../../componentes/SeletorIdioma'
import { useT, type Chave } from '../../i18n'
import { useAcaoNoCartao } from '../cartao/acoes'
import { FormProjeto } from './FormProjeto'
import { NovaDemanda } from './NovaDemanda'
import { FASES, faseDoCartao, type Fase } from './fase'

export function TelaProjeto() {
  const { projetoId = '' } = useParams()
  const { produto, rota } = useNagare()
  const { t, eu, data } = useT()
  const [editar, setEditar] = useState(false)
  const [demanda, setDemanda] = useState(false)
  const projeto = useQuery({
    queryKey: chaves.projeto(produto, projetoId),
    queryFn: () => nagare<ProjetoDetalhe>(produto, `/projects/${encodeURIComponent(projetoId)}`),
  })
  if (projeto.isPending) return <Carregando texto={t('comum.carregando')} />
  if (projeto.isError) return <ErroDeApi erro={projeto.error} />
  const p = projeto.data
  const capacidades = eu?.capabilities ?? {}
  const porFase = new Map<Fase, Cartao[]>(FASES.map((f) => [f, []]))
  for (const cartao of p.cards) porFase.get(faseDoCartao(cartao))!.push(cartao)

  return (
    <>
      <Cabecalho
        kicker={`${t('projetos.titulo')} · ${p.key}`}
        titulo={p.name}
        descricao={p.description ?? undefined}
        acoes={
          <>
            <Link className="btn sm ghost" to={rota('projetos')}>
              {t('projetos.voltar')}
            </Link>
            {capacidades.manage_cards && eu?.flags.envelope !== false && (
              <button type="button" className="btn sm" onClick={() => setDemanda(true)}>
                {t('demanda.nova')}
              </button>
            )}
            {capacidades.manage_boards && (
              <button type="button" className="btn sm sec" onClick={() => setEditar(true)}>
                {t('comum.editar')}
              </button>
            )}
            <SeletorIdioma />
          </>
        }
      />
      <p className="mini">
        <Tag>{t(`projeto.situacao.${p.status}`)}</Tag>{' '}
        {p.started_at && t('projeto.iniciado', { quando: data(p.started_at) })}{' '}
        {p.target_date && t('projeto.alvo_em', { quando: data(p.target_date) })}
      </p>
      {p.cards.length === 0 ? (
        <section className="card">
          <Vazio titulo={t('projeto.sem_cartoes')} />
        </section>
      ) : (
        FASES.map((fase) => {
          const cartoes = porFase.get(fase) ?? []
          if (cartoes.length === 0) return null
          return (
            <section key={fase} className="card">
              <h2 className="ng-subtitulo">
                {t(`projeto.fase.${fase}` as Chave)} <span className="mini">({cartoes.length})</span>
              </h2>
              <ul className="ng-demandas">
                {cartoes.map((cartao) => (
                  <LinhaDemanda key={cartao.id} cartao={cartao} podeAbrir={Boolean(capacidades.manage_cards)} />
                ))}
              </ul>
            </section>
          )
        })
      )}
      {editar && <FormProjeto projeto={p} fechar={() => setEditar(false)} />}
      {demanda && <NovaDemanda projeto={p} fechar={() => setDemanda(false)} />}
    </>
  )
}

function LinhaDemanda({ cartao, podeAbrir }: { cartao: Cartao; podeAbrir: boolean }) {
  const { t, data } = useT()
  const { rota } = useNagare()
  const lacrado = cartao.envelope_state === 'sealed'
  const abrirEnvelope = useAcaoNoCartao(cartao, 'cartao.envelope_aberto')
  return (
    <li className={`ng-demanda classe-${cartao.service_class}`}>
      <span className="ng-chave">{cartao.key}</span>
      <Link to={`${rota(`quadro/${cartao.board_id}`)}?cartao=${cartao.id}`}>
        {lacrado ? t('cartao.lacrado') : cartao.title}
      </Link>
      <span className="mini">{t(`classe.${cartao.service_class}`)}</span>
      {cartao.is_blocked && <Tag cor="vermelho">{t('cartao.bloqueado')}</Tag>}
      {cartao.due_date && <span className="mini">{t('cartao.prazo_em', { quando: data(cartao.due_date) })}</span>}
      {cartao.assignee_name && <span className="mini">{cartao.assignee_name}</span>}
      {lacrado && podeAbrir && (
        <button
          type="button"
          className="btn sm sec"
          disabled={abrirEnvelope.isPending}
          onClick={() => abrirEnvelope.mutate({ caminho: `/cards/${cartao.id}/open-envelope`, metodo: 'POST' })}
        >
          {t('cartao.abrir_envelope')}
        </button>
      )}
    </li>
  )
}
