/** Horas do cartao (GJ-4): quem, quando, quanto e o que. Cada pessoa lanca as suas. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Aviso, ErroDeApi } from '@pampufa/ui'

import { nagare } from '../../api/nagare'
import type { CartaoDetalhe, HoraLancada } from '../../api/tipos'
import { useNagare } from '../../app/contexto'
import { mensagemDeErro, useT } from '../../i18n'
import { horasEmTexto, minutosDeTexto } from './horas'

function hojeISO(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function AbaHoras({ cartao, editavel }: { cartao: CartaoDetalhe; editavel: boolean }) {
  const { t, data, eu } = useT()
  const produto = useNagare().produto
  const queryClient = useQueryClient()
  const chave = ['nagare', produto, 'cartao', cartao.id, 'horas'] as const
  const horas = useQuery({
    queryKey: chave,
    queryFn: () => nagare<{ items: HoraLancada[]; total_minutes: number }>(produto, `/cards/${cartao.id}/time`),
  })
  const [dia, setDia] = useState(hojeISO())
  const [quanto, setQuanto] = useState('')
  const [descricao, setDescricao] = useState('')
  const minutos = minutosDeTexto(quanto)
  const lancar = useMutation({
    mutationFn: () =>
      nagare(produto, `/cards/${cartao.id}/time`, { metodo: 'POST', corpo: { worked_on: dia, minutes: minutos, description: descricao.trim() || null } }),
    onSuccess: () => {
      setQuanto('')
      setDescricao('')
      void queryClient.invalidateQueries({ queryKey: chave })
    },
  })
  const remover = useMutation({
    mutationFn: (id: string) => nagare(produto, `/time/${id}`, { metodo: 'DELETE' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: chave }),
  })
  const admin = eu?.role === 'master' || eu?.role === 'administrator'
  return (
    <div>
      {horas.isError && <ErroDeApi erro={horas.error} />}
      {horas.data && (
        <p className="mini">{t('horas.total', { total: horasEmTexto(horas.data.total_minutes) })}</p>
      )}
      {horas.data && horas.data.items.length === 0 && <p className="mini">{t('horas.nenhuma')}</p>}
      <ul className="ng-tarefas-lista">
        {(horas.data?.items ?? []).map((h) => (
          <li key={h.id}>
            <span>
              <b>{horasEmTexto(h.minutes)}</b> · {data(h.worked_on)} · {h.user_name}
              {h.description && <span className="mini"> — {h.description}</span>}
            </span>
            {editavel && (admin || h.user_id === eu?.id) && (
              <button type="button" className="link" onClick={() => remover.mutate(h.id)}>
                {t('comum.remover')}
              </button>
            )}
          </li>
        ))}
      </ul>
      {editavel && (
        <form
          className="ng-par"
          style={{ flexWrap: 'wrap' }}
          onSubmit={(e) => {
            e.preventDefault()
            if (minutos) lancar.mutate()
          }}
        >
          <input type="date" aria-label={t('horas.dia')} value={dia} max={hojeISO()} onChange={(e) => setDia(e.target.value)} />
          <input aria-label={t('horas.quanto')} placeholder={t('horas.quanto_exemplo')} value={quanto} maxLength={8}
            onChange={(e) => setQuanto(e.target.value)} style={{ width: 90 }} />
          <input aria-label={t('horas.descricao')} placeholder={t('horas.descricao')} value={descricao} maxLength={2000}
            onChange={(e) => setDescricao(e.target.value)} style={{ flex: 1, minWidth: 160 }} />
          <button className="btn sm" type="submit" disabled={!minutos || lancar.isPending}>{t('horas.lancar')}</button>
        </form>
      )}
      {quanto && !minutos && <Aviso tipo="alerta">{t('horas.invalido')}</Aviso>}
      {lancar.isError && <Aviso tipo="erro">{mensagemDeErro(lancar.error, t)}</Aviso>}
    </div>
  )
}
