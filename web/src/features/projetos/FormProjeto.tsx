/** Criar ou editar um projeto do produto. */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Aviso } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import { SITUACOES_PROJETO, type Projeto, type SituacaoProjeto } from '../../api/tipos'
import { Janela } from '../../componentes/Sobreposicao'
import { mensagemDeErro, useT } from '../../i18n'

export function FormProjeto({
  projeto,
  fechar,
  criado,
}: {
  projeto: Projeto | null
  fechar: () => void
  criado?: (projeto: Projeto) => void
}) {
  const { t } = useT()
  const produto = useNagare().produto
  const queryClient = useQueryClient()
  const [nome, setNome] = useState(projeto?.name ?? '')
  const [chave, setChave] = useState(projeto?.key ?? '')
  const [cor, setCor] = useState(projeto?.color ?? '#24548f')
  const [situacao, setSituacao] = useState<SituacaoProjeto>(projeto?.status ?? 'active')
  const [descricao, setDescricao] = useState(projeto?.description ?? '')
  const [inicio, setInicio] = useState(projeto?.started_at?.slice(0, 10) ?? '')
  const [alvo, setAlvo] = useState(projeto?.target_date?.slice(0, 10) ?? '')
  const gravar = useMutation({
    mutationFn: () => {
      const corpo = {
        name: nome.trim(),
        color: cor,
        status: situacao,
        description: descricao.trim() || null,
        started_at: inicio || null,
        target_date: alvo || null,
      }
      return projeto
        ? nagare<Projeto>(produto, `/projects/${projeto.id}`, { metodo: 'PATCH', corpo })
        : nagare<Projeto>(produto, '/projects', { metodo: 'POST', corpo: { ...corpo, key: chave.trim().toUpperCase() } })
    },
    onSuccess: (salvo) => {
      void queryClient.invalidateQueries({ queryKey: chaves.projetos(produto) })
      void queryClient.invalidateQueries({ queryKey: chaves.projeto(produto, salvo.id) })
      criado?.(salvo)
      fechar()
    },
  })
  const valido = nome.trim() && (projeto || /^[A-Z0-9]{2,8}$/.test(chave.trim().toUpperCase()))

  return (
    <Janela
      titulo={projeto ? t('projetos.editar', { chave: projeto.key }) : t('projetos.novo')}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" type="submit" form="form-projeto" disabled={!valido || gravar.isPending}>
            {t('comum.salvar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <form
        id="form-projeto"
        onSubmit={(e) => {
          e.preventDefault()
          if (valido) gravar.mutate()
        }}
      >
        <label className="campo">
          <span>{t('projeto.nome')}</span>
          <input value={nome} maxLength={200} autoFocus required onChange={(e) => setNome(e.target.value)} />
        </label>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('projeto.chave')}</span>
            <input
              value={chave}
              maxLength={8}
              disabled={Boolean(projeto)}
              required
              onChange={(e) => setChave(e.target.value.toUpperCase())}
            />
            {!projeto && <small className="mini">{t('projeto.chave_dica')}</small>}
          </label>
          <label className="campo">
            <span>{t('projeto.situacao')}</span>
            <select value={situacao} onChange={(e) => setSituacao(e.target.value as SituacaoProjeto)}>
              {SITUACOES_PROJETO.map((s) => (
                <option key={s} value={s}>
                  {t(`projeto.situacao.${s}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="campo">
            <span>{t('estrutura.cor')}</span>
            <input type="color" value={cor} onChange={(e) => setCor(e.target.value)} />
          </label>
        </div>
        <label className="campo">
          <span>{t('cartao.campo.descricao')}</span>
          <textarea rows={3} value={descricao} onChange={(e) => setDescricao(e.target.value)} />
        </label>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('projeto.inicio')}</span>
            <input type="date" value={inicio} onChange={(e) => setInicio(e.target.value)} />
          </label>
          <label className="campo">
            <span>{t('projeto.alvo')}</span>
            <input type="date" value={alvo} onChange={(e) => setAlvo(e.target.value)} />
          </label>
        </div>
      </form>
      {gravar.isError && <Aviso tipo="erro">{mensagemDeErro(gravar.error, t)}</Aviso>}
    </Janela>
  )
}
