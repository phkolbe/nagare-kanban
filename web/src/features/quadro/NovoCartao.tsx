/** Novo cartao numa coluna: titulo, projeto e classe; o resto se edita no cartao. */

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Aviso, useProduto } from '@pampufa/ui'

import { nagare } from '../../api/nagare'
import { CLASSES, type Cartao, type ClasseServico, type Coluna } from '../../api/tipos'
import { Janela } from '../../componentes/Sobreposicao'
import { mensagemDeErro, useT } from '../../i18n'
import { useProjetos, useRecarregarQuadro } from './consultas'

export function NovoCartao({
  quadroId,
  coluna,
  classe,
  fechar,
  criado,
}: {
  quadroId: string
  coluna: Coluna
  classe?: ClasseServico
  fechar: () => void
  criado: (cartao: Cartao) => void
}) {
  const { t, eu } = useT()
  const produto = useProduto().id
  const projetos = useProjetos()
  const recarregar = useRecarregarQuadro()
  const [titulo, setTitulo] = useState('')
  const [projeto, setProjeto] = useState('')
  const [classeEscolhida, setClasse] = useState<ClasseServico>(classe ?? 'standard')
  const criar = useMutation({
    mutationFn: () =>
      nagare<Cartao>(produto, '/cards', {
        metodo: 'POST',
        corpo: {
          board_id: quadroId,
          column_id: coluna.id,
          title: titulo.trim(),
          project_id: projeto || null,
          service_class: classeEscolhida,
        },
      }),
    onSuccess: (cartao) => {
      recarregar(quadroId)
      criado(cartao)
    },
  })

  return (
    <Janela
      titulo={t('novo.titulo', { coluna: coluna.name })}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" type="submit" form="novo-cartao" disabled={!titulo.trim() || criar.isPending}>
            {t('novo.criar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <form
        id="novo-cartao"
        onSubmit={(e) => {
          e.preventDefault()
          if (titulo.trim()) criar.mutate()
        }}
      >
        <label className="campo">
          <span>{t('cartao.campo.titulo')}</span>
          <input value={titulo} maxLength={500} autoFocus required onChange={(e) => setTitulo(e.target.value)} />
        </label>
        {eu?.flags.multi_project !== false && (
          <label className="campo">
            <span>{t('cartao.campo.projeto')}</span>
            <select value={projeto} onChange={(e) => setProjeto(e.target.value)}>
              <option value="">{t('cartao.sem_projeto')}</option>
              {(projetos.data?.items ?? [])
                .filter((p) => !p.archived)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.key} · {p.name}
                  </option>
                ))}
            </select>
          </label>
        )}
        {eu?.flags.service_classes !== false && (
          <label className="campo">
            <span>{t('cartao.campo.classe')}</span>
            <select value={classeEscolhida} onChange={(e) => setClasse(e.target.value as ClasseServico)}>
              {CLASSES.map((c) => (
                <option key={c} value={c}>
                  {t(`classe.${c}`)}
                </option>
              ))}
            </select>
            <small className="mini">{t(`classe.${classeEscolhida}.politica`)}</small>
          </label>
        )}
      </form>
      {criar.isError && <Aviso tipo="erro">{mensagemDeErro(criar.error, t)}</Aviso>}
    </Janela>
  )
}
