/** D45: pessoas, projetos e clientes num periodo (horas, entregas, prazo e demanda). */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Carregando, ErroDeApi } from '@pampufa/ui'

import { nagare } from '../../api/nagare'
import type { LeituraPessoas, LinhaDaLeitura } from '../../api/tipos'
import { useNagare } from '../../app/contexto'
import { useT } from '../../i18n'
import { horasEmTexto } from '../cartao/horas'

function diaISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** O inicio do mes de hoje e hoje: o periodo padrao (o mes que a fatura vai cobrar). */
export function periodoPadrao(hoje = new Date()): { de: string; ate: string } {
  return { de: diaISO(new Date(hoje.getFullYear(), hoje.getMonth(), 1)), ate: diaISO(hoje) }
}

/** "2 de 3 no prazo (67%)" ou "—" sem entrega de data fixa. */
export function textoNoPrazo(linha: Pick<LinhaDaLeitura, 'on_time' | 'late'>): string {
  const total = linha.on_time + linha.late
  if (!total) return '—'
  return `${linha.on_time} de ${total} (${Math.round((linha.on_time / total) * 100)}%)`
}

export function PainelPessoas() {
  const { t } = useT()
  const produto = useNagare().produto
  const [periodo, setPeriodo] = useState(periodoPadrao())
  const leitura = useQuery({
    queryKey: ['nagare', produto, 'pessoas-projetos', periodo.de, periodo.ate],
    queryFn: () => nagare<LeituraPessoas>(produto, `/metrics/people?from=${periodo.de}&to=${periodo.ate}`),
  })
  const tabela = (titulo: string, linhas: (LinhaDaLeitura & { nome: string; extra?: string })[]) => (
    <div className="tabela-rolagem">
      <table>
        <thead>
          <tr>
            <th>{titulo}</th>
            <th className="num">{t('pessoas.horas')}</th>
            <th className="num">{t('pessoas.entregues')}</th>
            <th className="num">{t('pessoas.no_prazo')}</th>
            <th className="num">{t('pessoas.em_andamento')}</th>
            <th className="num">{t('pessoas.chegaram')}</th>
          </tr>
        </thead>
        <tbody>
          {linhas.map((l) => (
            <tr key={l.nome + (l.extra ?? '')}>
              <td>{l.nome}{l.extra && <div className="mini">{l.extra}</div>}</td>
              <td className="num">{horasEmTexto(l.minutes)}</td>
              <td className="num">{l.delivered}</td>
              <td className="num">{textoNoPrazo(l)}</td>
              <td className="num">{l.wip}</td>
              <td className="num">{l.arrivals}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
  return (
    <section className="card" aria-label={t('pessoas.titulo')}>
      <h2 className="ng-subtitulo">{t('pessoas.titulo')}</h2>
      <div className="ng-par" style={{ flexWrap: 'wrap' }}>
        <label className="mini">{t('pessoas.de')} <input type="date" value={periodo.de} onChange={(e) => setPeriodo({ ...periodo, de: e.target.value })} /></label>
        <label className="mini">{t('pessoas.ate')} <input type="date" value={periodo.ate} onChange={(e) => setPeriodo({ ...periodo, ate: e.target.value })} /></label>
      </div>
      {leitura.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : leitura.isError ? (
        <ErroDeApi erro={leitura.error} />
      ) : (
        <>
          <p className="mini">{t('pessoas.total', { total: horasEmTexto(leitura.data.total_minutes) })}</p>
          <h3>{t('pessoas.por_pessoa')}</h3>
          {tabela(t('pessoas.pessoa'), leitura.data.people.map((p) => ({ ...p, nome: p.name })))}
          <h3>{t('pessoas.por_projeto')}</h3>
          {tabela(t('pessoas.projeto'), leitura.data.projects.map((p) => ({ ...p, nome: p.key ? `${p.key} · ${p.name}` : p.name, extra: p.client_name ?? undefined })))}
          <h3>{t('pessoas.por_cliente')}</h3>
          {tabela(t('pessoas.cliente'), leitura.data.clients.map((c) => ({ ...c, nome: c.client_name, extra: t('pessoas.n_projetos', { n: c.projects }) })))}
        </>
      )}
    </section>
  )
}
