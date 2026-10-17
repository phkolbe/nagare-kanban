/** Nova demanda do projeto: entra como envelope lacrado na coluna de entrada do quadro (SPEC §5). */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Aviso } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import {
  AREAS_VALOR,
  CLASSES,
  TAMANHOS,
  TIPOS_DEMANDA,
  type AreaValor,
  type Cartao,
  type ClasseServico,
  type ProjetoDetalhe,
  type Tamanho,
  type TipoDemanda,
} from '../../api/tipos'
import { Janela } from '../../componentes/Sobreposicao'
import { mensagemDeErro, useT } from '../../i18n'
import { useQuadros } from '../quadro/consultas'

export function NovaDemanda({ projeto, fechar }: { projeto: ProjetoDetalhe; fechar: () => void }) {
  const { t } = useT()
  const produto = useNagare().produto
  const queryClient = useQueryClient()
  const quadros = useQuadros()
  const [f, setF] = useState({
    titulo: '',
    descricao: '',
    solicitante: '',
    classe: 'standard' as ClasseServico,
    tipo: 'value' as TipoDemanda,
    area: 'feature' as AreaValor,
    tamanho: '' as Tamanho | '',
    prazo: '',
    etiquetas: '',
    lacrado: true,
    quadro: '',
  })
  const criar = useMutation({
    mutationFn: () =>
      nagare<Cartao>(produto, `/projects/${projeto.id}/demands`, {
        metodo: 'POST',
        corpo: {
          title: f.titulo.trim(),
          description: f.descricao.trim() || null,
          requester: f.solicitante.trim() || null,
          service_class: f.classe,
          demand_type: f.tipo,
          value_area: f.area,
          size: f.tamanho || null,
          due_date: f.prazo || null,
          tags: f.etiquetas.split(',').map((x) => x.trim()).filter(Boolean),
          sealed: f.lacrado,
          board_id: f.quadro || null,
        },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: chaves.projeto(produto, projeto.id) })
      void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'visao'] })
      fechar()
    },
  })
  const muda = <K extends keyof typeof f>(campo: K, valor: (typeof f)[K]) => setF({ ...f, [campo]: valor })

  return (
    <Janela
      titulo={t('demanda.titulo', { projeto: projeto.key })}
      fechar={fechar}
      largura={600}
      rodape={
        <>
          <button className="btn" type="submit" form="nova-demanda" disabled={!f.titulo.trim() || criar.isPending}>
            {t('demanda.criar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <form
        id="nova-demanda"
        onSubmit={(e) => {
          e.preventDefault()
          if (f.titulo.trim()) criar.mutate()
        }}
      >
        <label className="campo">
          <span>{t('cartao.campo.titulo')}</span>
          <input value={f.titulo} maxLength={500} autoFocus required onChange={(e) => muda('titulo', e.target.value)} />
        </label>
        <label className="campo">
          <span>{t('cartao.campo.descricao')}</span>
          <textarea rows={3} value={f.descricao} onChange={(e) => muda('descricao', e.target.value)} />
        </label>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('cartao.campo.solicitante')}</span>
            <input value={f.solicitante} maxLength={200} onChange={(e) => muda('solicitante', e.target.value)} />
          </label>
          <label className="campo">
            <span>{t('demanda.quadro')}</span>
            <select value={f.quadro} onChange={(e) => muda('quadro', e.target.value)}>
              <option value="">{t('demanda.quadro_padrao')}</option>
              {(quadros.data?.items ?? [])
                .filter((q) => !q.archived && !q.read_only)
                .map((q) => (
                  <option key={q.id} value={q.id}>
                    {q.name}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('cartao.campo.classe')}</span>
            <select value={f.classe} onChange={(e) => muda('classe', e.target.value as ClasseServico)}>
              {CLASSES.map((c) => (
                <option key={c} value={c}>
                  {t(`classe.${c}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="campo">
            <span>{t('cartao.campo.tipo')}</span>
            <select value={f.tipo} onChange={(e) => muda('tipo', e.target.value as TipoDemanda)}>
              {TIPOS_DEMANDA.map((x) => (
                <option key={x} value={x}>
                  {t(`tipo_demanda.${x}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="campo">
            <span>{t('cartao.campo.area')}</span>
            <select value={f.area} onChange={(e) => muda('area', e.target.value as AreaValor)}>
              {AREAS_VALOR.map((x) => (
                <option key={x} value={x}>
                  {t(`area.${x}`)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('cartao.campo.tamanho')}</span>
            <select value={f.tamanho} onChange={(e) => muda('tamanho', e.target.value as Tamanho | '')}>
              <option value="">—</option>
              {TAMANHOS.map((x) => (
                <option key={x} value={x}>
                  {x.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <label className="campo">
            <span>{t('cartao.campo.prazo')}</span>
            <input type="date" value={f.prazo} onChange={(e) => muda('prazo', e.target.value)} />
          </label>
        </div>
        <label className="campo">
          <span>{t('cartao.campo.etiquetas')}</span>
          <input value={f.etiquetas} onChange={(e) => muda('etiquetas', e.target.value)} />
          <small className="mini">{t('cartao.etiquetas_dica')}</small>
        </label>
        <label className="ng-alternar">
          <input type="checkbox" checked={f.lacrado} onChange={(e) => muda('lacrado', e.target.checked)} />
          {t('demanda.lacrado')}
        </label>
      </form>
      {criar.isError && <Aviso tipo="erro">{mensagemDeErro(criar.error, t)}</Aviso>}
    </Janela>
  )
}
