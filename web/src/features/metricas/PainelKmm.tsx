/**
 * Kanban Maturity Model (flag `kmm_dashboard`, paridade com a tela antiga): o nivel atual, o que
 * falta para o proximo, a escada ML0-ML6 com a prontidao de cada nivel e o detalhe ao clicar.
 */

import { useState } from 'react'
import { Tag, Vazio } from '@pampufa/ui'

import { Anel } from '../../graficos/Graficos'
import { useT } from '../../i18n'
import type { Kmm } from './consultas'
import { valorKmm } from './formato'

export function PainelKmm({ kmm }: { kmm: Kmm }) {
  const { t, numero } = useT()
  const atualIndice = kmm.levels.findIndex((n) => n.level === kmm.current_level)
  const [aberto, setAberto] = useState<string | null>(kmm.next_level ?? kmm.current_level ?? null)
  if (kmm.levels.length === 0) return <Vazio titulo={t('kmm.sem_dados')} />
  const detalhe = kmm.levels.find((n) => n.level === aberto)
  const faltam = detalhe?.metrics.filter((m) => !m.available) ?? []

  return (
    <div>
      <div className="ng-kmm-status">
        {kmm.next_level ? (
          <>
            <b>{t('kmm.atual', { nivel: kmm.current_level ?? '—', nome: kmm.current_level_name ?? '' })}</b>
            <span className="mini">{t('kmm.travado_em', { nivel: kmm.next_level })}</span>
            {kmm.next_actions.length > 0 && (
              <ul className="ng-kmm-acoes">
                {kmm.next_actions.slice(0, 3).map((acao) => (
                  <li key={acao}>{acao}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <b>{t('kmm.completo')}</b>
        )}
      </div>
      <ol className="ng-kmm-escada">
        {kmm.levels.map((nivel, i) => (
          <li key={nivel.level} className={i > atualIndice + 1 ? 'apagado' : undefined}>
            <button type="button" aria-expanded={aberto === nivel.level} onClick={() => setAberto(aberto === nivel.level ? null : nivel.level)}>
              <Anel
                fracao={nivel.readiness_pct / 100}
                rotulo={t('kmm.pronto', { pct: numero(nivel.readiness_pct, 0) })}
                cor={nivel.readiness_pct >= 100 ? 'var(--green)' : 'var(--amber)'}
              />
              <span>
                <b>{nivel.level}</b> {nivel.name}
              </span>
              {nivel.level === kmm.current_level && <Tag cor="azul">{t('kmm.atual_tag')}</Tag>}
            </button>
          </li>
        ))}
      </ol>
      {detalhe && (
        <div className="ng-kmm-detalhe">
          <h3>
            {detalhe.level} · {detalhe.name}
          </h3>
          <p className="mini">{detalhe.focus}</p>
          <p className="mini">
            {t('kmm.prontidao', {
              pct: numero(detalhe.readiness_pct, 0),
              feitas: detalhe.metrics.filter((m) => m.available).length,
              total: detalhe.metrics.length,
            })}
          </p>
          <ul className="ng-kmm-metricas">
            {detalhe.metrics.map((m) => (
              <li key={m.key} className={m.available ? 'ok' : 'falta'}>
                <span aria-hidden="true">{m.available ? '✓' : '✗'}</span>
                <span className="visualmente-oculto">{m.available ? t('kmm.disponivel') : t('kmm.indisponivel')}</span>
                <b>{m.label}</b>
                <span className="mono">{m.available ? valorKmm(m.value, numero) : '—'}</span>
                {m.why && <span className="mini">{m.why}</span>}
              </li>
            ))}
          </ul>
          {faltam.length > 0 && (
            <p className="ng-kmm-falta">
              <b>{t('kmm.para_subir')}</b> {faltam.map((m) => m.label).join(', ')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
