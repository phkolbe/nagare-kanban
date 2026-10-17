/** A mini linha de vazao semanal de um projeto (cartao da lista de projetos, paridade). */

import { useQuery } from '@tanstack/react-query'
import { hojeISO } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { consulta, nagare } from '../../api/nagare'
import { MiniLinha } from '../../graficos/Graficos'
import { useT } from '../../i18n'
import { janela, type Series } from '../metricas/consultas'

export function VazaoDoProjeto({ projetoId, quadroId }: { projetoId: string; quadroId: string }) {
  const { t, eu } = useT()
  const { produto } = useNagare()
  const ligado = eu?.flags.metrics_basic !== false
  const { from, to } = janela('90d', hojeISO(), null)
  const busca = consulta({ from, to, project_id: projetoId, period: 'week' })
  const vazao = useQuery({
    queryKey: ['nagare', produto, 'metricas', quadroId, 'throughput-projeto', busca],
    queryFn: () => nagare<Series>(produto, `/boards/${encodeURIComponent(quadroId)}/metrics/throughput${busca}`),
    enabled: ligado,
    staleTime: 300_000,
  })
  const pontos = (vazao.data?.series.find((s) => s.mark === 'bar') ?? vazao.data?.series[0])?.points ?? []
  const valores = pontos.slice(-12).map((p) => Number(p.y) || 0)
  if (!ligado || valores.length < 2) return null
  return (
    <div className="ng-vazao-projeto">
      <MiniLinha valores={valores} rotulo={t('projetos.vazao')} />
      <span className="mini">{t('projetos.vazao')}</span>
    </div>
  )
}
