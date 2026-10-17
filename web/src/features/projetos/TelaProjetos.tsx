/**
 * Projetos do produto (`/grupo-sr/<produto>/tarefas/projetos`) e qual deles recebe as
 * providencias (NG14): um projeto ativo por produto; fluxo novo e projeto novo.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Aviso, Cabecalho, Carregando, ErroDeApi, Tag, Vazio, useProduto } from '@pampufa/ui'

import { SITUACOES_PROJETO, type Projeto, type SituacaoProjeto } from '../../api/tipos'
import { SeletorIdioma } from '../../componentes/SeletorIdioma'
import { useT } from '../../i18n'
import { useProjetos, useVinculo } from '../quadro/consultas'
import { FormProjeto } from './FormProjeto'
import { TrocarFluxo } from './TrocarFluxo'

export function TelaProjetos() {
  const { t, eu, dataHora } = useT()
  const produto = useProduto().id
  const projetos = useProjetos()
  const vinculo = useVinculo()
  const [situacao, setSituacao] = useState<SituacaoProjeto | ''>('')
  const [editando, setEditando] = useState<Projeto | 'novo' | null>(null)
  const [trocar, setTrocar] = useState(false)
  const capacidades = eu?.capabilities ?? {}
  const podeGerir = Boolean(capacidades.manage_boards)
  const podeTrocar = ['master', 'administrator'].includes(eu?.role ?? '')

  const lista = (projetos.data?.items ?? []).filter((p) => !situacao || p.status === situacao)
  const link = vinculo.data?.link

  return (
    <>
      <Cabecalho
        kicker={t('quadro.kicker')}
        titulo={t('projetos.titulo')}
        descricao={t('projetos.descricao')}
        acoes={
          <>
            {podeGerir && eu?.flags.multi_project !== false && (
              <button type="button" className="btn sm" onClick={() => setEditando('novo')}>
                {t('projetos.novo')}
              </button>
            )}
            <SeletorIdioma />
          </>
        }
      />

      {produto !== 'nagare' && (
        <section className="card">
          <h2 className="ng-subtitulo">{t('vinculo.titulo')}</h2>
          {vinculo.isPending ? (
            <Carregando texto={t('comum.carregando')} />
          ) : vinculo.isError ? (
            <ErroDeApi erro={vinculo.error} />
          ) : link ? (
            <>
              <p>
                {t('vinculo.atual', { projeto: `${link.project.key} · ${link.project.name}`, quadro: link.board.name })}{' '}
                <Link className="link" to={`/${produto}/tarefas/quadro/${link.board.id}`}>
                  {t('vinculo.abrir_quadro')}
                </Link>
              </p>
              <p className="mini">{t('vinculo.desde', { quando: dataHora(link.linked_at) })}</p>
              {podeTrocar && (
                <button type="button" className="btn sm sec" onClick={() => setTrocar(true)}>
                  {t('vinculo.trocar')}
                </button>
              )}
            </>
          ) : (
            <Aviso tipo="info">{t('vinculo.sem_vinculo')}</Aviso>
          )}
          {(vinculo.data?.history.length ?? 0) > 0 && (
            <details className="ng-historico-vinculo">
              <summary>{t('vinculo.historico')}</summary>
              <ul className="ng-linha-do-tempo">
                {[...(vinculo.data?.history ?? [])].reverse().map((h) => (
                  <li key={h.id}>
                    <time className="mini">{dataHora(h.at)}</time>
                    <div>
                      {t(h.action === 'linked' ? 'vinculo.ligado' : 'vinculo.desligado', { cartoes: h.moved_cards })}
                      {h.reason && <div className="mini">“{h.reason}”</div>}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      <div className="abas" role="tablist" aria-label={t('projetos.situacao')}>
        {(['', ...SITUACOES_PROJETO] as const).map((s) => (
          <button key={s || 'todos'} type="button" role="tab" aria-selected={situacao === s} onClick={() => setSituacao(s)}>
            {s ? t(`projeto.situacao.${s}`) : t('filtros.todos')}
          </button>
        ))}
      </div>

      {projetos.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : projetos.isError ? (
        <ErroDeApi erro={projetos.error} />
      ) : lista.length === 0 ? (
        <section className="card">
          <Vazio titulo={t('projetos.nenhum')} />
        </section>
      ) : (
        <div className="ng-projetos">
          {lista.map((p) => (
            <article key={p.id} className="card ng-projeto-cartao" style={{ borderTopColor: p.color ?? undefined }}>
              <div className="ng-projeto-topo">
                <span className="ng-chave">{p.key}</span>
                <Tag cor={p.status === 'active' ? 'verde' : p.status === 'on_hold' ? 'amarelo' : 'cinza'}>
                  {t(`projeto.situacao.${p.status}`)}
                </Tag>
                {link?.project_id === p.id && <Tag cor="azul">{t('vinculo.recebe')}</Tag>}
              </div>
              <h3>
                <Link to={p.id}>{p.name}</Link>
              </h3>
              {p.description && <p className="mini">{p.description}</p>}
              <dl className="ng-numeros">
                <div>
                  <dt>{t('projeto.lacrados')}</dt>
                  <dd>{p.sealed_count ?? 0}</dd>
                </div>
                <div>
                  <dt>{t('projeto.em_andamento')}</dt>
                  <dd>{p.in_flight_count ?? 0}</dd>
                </div>
                <div>
                  <dt>{t('projeto.entregues')}</dt>
                  <dd>{p.delivered_count ?? 0}</dd>
                </div>
              </dl>
              {podeGerir && (
                <button type="button" className="link" onClick={() => setEditando(p)}>
                  {t('comum.editar')}
                </button>
              )}
            </article>
          ))}
        </div>
      )}

      {editando && <FormProjeto projeto={editando === 'novo' ? null : editando} fechar={() => setEditando(null)} />}
      {trocar && link && <TrocarFluxo atual={link} fechar={() => setTrocar(false)} />}
    </>
  )
}
