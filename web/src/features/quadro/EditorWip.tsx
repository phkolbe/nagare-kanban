/**
 * Selo de WIP (coluna, grupo ou fluxo) e o editor dos limites. WIP segue editavel com o fluxo
 * travado (NG13): e politica, nao estrutura.
 */

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useProduto } from '@pampufa/ui'

import { nagare } from '../../api/nagare'
import type { CelulaWip } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { mensagemDeErro, useT } from '../../i18n'
import { useRecarregarQuadro } from './consultas'

export type AlvoWip =
  | { tipo: 'coluna'; id: string; quadroId: string }
  | { tipo: 'grupo'; id: string; quadroId: string }
  | { tipo: 'fluxo'; quadroId: string }

export function SeloWip({ celula, compacto = false }: { celula: CelulaWip; compacto?: boolean }) {
  const { t } = useT()
  const limite = celula.max ?? '∞'
  return (
    <span
      className={`ng-wip ${celula.state}${compacto ? ' compacto' : ''}`}
      title={t(`wip.${celula.state}`, { atual: celula.count, max: celula.max ?? '∞', min: celula.min ?? 0 })}
    >
      {celula.count}/{limite}
    </span>
  )
}

export function EditorWip({
  alvo,
  celula,
  podeEditar,
}: {
  alvo: AlvoWip
  celula: CelulaWip
  podeEditar: boolean
}) {
  const { t } = useT()
  const produto = useProduto().id
  const avisar = useAvisos()
  const recarregar = useRecarregarQuadro()
  const [aberto, setAberto] = useState(false)
  const [minimo, setMinimo] = useState(celula.min?.toString() ?? '')
  const [maximo, setMaximo] = useState(celula.max?.toString() ?? '')
  const caminho =
    alvo.tipo === 'coluna' ? `/columns/${alvo.id}` : alvo.tipo === 'grupo' ? `/groups/${alvo.id}` : `/boards/${alvo.quadroId}`
  const gravar = useMutation({
    mutationFn: () =>
      nagare(produto, caminho, {
        metodo: 'PATCH',
        corpo: { wip_min: minimo === '' ? null : Number(minimo), wip_max: maximo === '' ? null : Number(maximo) },
      }),
    onSuccess: () => {
      setAberto(false)
      avisar(t('wip.gravado'), 'ok')
      recarregar(alvo.quadroId)
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })

  if (!podeEditar) return <SeloWip celula={celula} />
  return (
    <span className="ng-wip-editor">
      <button
        type="button"
        className="ng-wip-botao"
        aria-expanded={aberto}
        aria-label={t('wip.editar')}
        onClick={(e) => {
          e.stopPropagation()
          setMinimo(celula.min?.toString() ?? '')
          setMaximo(celula.max?.toString() ?? '')
          setAberto(!aberto)
        }}
      >
        <SeloWip celula={celula} />
      </button>
      {aberto && (
        <form
          className="popover ng-wip-pop"
          onClick={(e) => e.stopPropagation()}
          onSubmit={(e) => {
            e.preventDefault()
            gravar.mutate()
          }}
        >
          <div className="popover-titulo">{t(`wip.titulo_${alvo.tipo}`)}</div>
          <div className="ng-linha-campos">
            <label className="campo">
              <span>{t('wip.minimo')}</span>
              <input type="number" min={0} value={minimo} onChange={(e) => setMinimo(e.target.value)} />
            </label>
            <label className="campo">
              <span>{t('wip.maximo')}</span>
              <input type="number" min={0} value={maximo} autoFocus onChange={(e) => setMaximo(e.target.value)} />
            </label>
          </div>
          <p className="mini">{t('wip.vazio_sem_limite')}</p>
          <div className="acoes">
            <button className="btn sm" type="submit" disabled={gravar.isPending}>
              {t('comum.salvar')}
            </button>
            <button className="btn sm ghost" type="button" onClick={() => setAberto(false)}>
              {t('comum.cancelar')}
            </button>
          </div>
        </form>
      )}
    </span>
  )
}
