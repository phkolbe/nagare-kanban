/**
 * Comparacao de runs (2 a 8): metrica x run, a base escolhida e a diferenca em %, melhor ou pior.
 * A base e a que a pessoa escolhe (a tela antiga ignorava a escolha; corrigido no servidor).
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Carregando, ErroDeApi } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { nagare } from '../../api/nagare'
import { Janela } from '../../componentes/Sobreposicao'
import { useT } from '../../i18n'
import type { Comparacao, Run } from './tipos'

export function Comparar({ ids, runs, fechar }: { ids: string[]; runs: Run[]; fechar: () => void }) {
  const { t, numero } = useT()
  const { produto } = useNagare()
  const [base, setBase] = useState(ids[0]!)
  const comparacao = useQuery({
    queryKey: ['nagare', produto, 'simulador', 'comparar', ids.join(','), base],
    queryFn: () =>
      nagare<Comparacao>(produto, '/simulator/compare', {
        metodo: 'POST',
        corpo: { run_ids: [base, ...ids.filter((id) => id !== base)], baseline_run_id: base },
      }),
  })
  const nome = (id: string) => {
    const r = runs.find((x) => x.id === id)
    return r?.name ?? (r ? t(`simulador.run.${r.kind}`) : id)
  }
  return (
    <Janela titulo={t('simulador.comparar')} fechar={fechar} largura={960}>
      <label className="campo ng-campo-curto">
        <span>{t('simulador.base')}</span>
        <select value={base} onChange={(e) => setBase(e.target.value)}>
          {ids.map((id) => (
            <option key={id} value={id}>
              {nome(id)}
            </option>
          ))}
        </select>
      </label>
      {comparacao.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : comparacao.isError ? (
        <ErroDeApi erro={comparacao.error} />
      ) : (
        <>
          <div className="tabela-rolagem">
            <table className="ng-tabela">
              <thead>
                <tr>
                  <th>{t('simulador.metrica')}</th>
                  {comparacao.data.rows[0]?.cells.map((c) => (
                    <th key={c.run_id}>
                      {nome(c.run_id)}
                      {c.run_id === base && <span className="mini"> · {t('simulador.base')}</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparacao.data.rows.map((linha) => (
                  <tr key={linha.key}>
                    <td>
                      {linha.label}
                      {linha.unit && <span className="mini"> ({linha.unit})</span>}
                    </td>
                    {linha.cells.map((c) => (
                      <td key={c.run_id}>
                        {c.value == null ? '—' : numero(c.value, 1)}
                        {c.run_id !== base && c.delta_pct != null && (
                          <span className={`ng-delta ${c.improved === null ? '' : c.improved ? 'ng-bom' : 'ng-ruim'}`}>
                            {c.delta_pct > 0 ? '+' : ''}
                            {numero(c.delta_pct, 1)}%
                          </span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {comparacao.data.verdict && <div className="ng-veredito">{comparacao.data.verdict}</div>}
        </>
      )}
    </Janela>
  )
}
