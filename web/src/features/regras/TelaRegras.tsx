/**
 * Regras de tarefa (NG9): para cada tipo de providencia, quem recebe o cartao, em quantos dias
 * vence e com que classe de servico. Moram no pampufa (dono da ponte), com historico; a mais
 * especifica vence e a curinga (`*`/`*`) e o responsavel padrao do produto.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Aviso, Cabecalho, Carregando, ErroDeApi, Tag, requisitar, useProduto } from '@pampufa/ui'

import { chaves, urlRegras } from '../../api/nagare'
import { CLASSES, type ClasseServico, type Regra, type Regras } from '../../api/tipos'
import { SeletorIdioma } from '../../componentes/SeletorIdioma'
import { Janela } from '../../componentes/Sobreposicao'
import { mensagemDeErro, useT, type Chave } from '../../i18n'

/** Servicos que abrem providencias (prefixo do evento `<servico>.providencia.aberta`). */
const SERVICOS = ['*', 'financeiro', 'sradv', 'batoom'] as const

export function TelaRegras() {
  const { t, dataHora } = useT()
  const produto = useProduto().id
  const regras = useQuery({
    queryKey: chaves.regras(produto),
    queryFn: () => requisitar<Regras>(urlRegras(produto)),
  })
  const [editando, setEditando] = useState<Regra | 'nova' | null>(null)

  const servico = (s: string) => (s === '*' ? t('regras.qualquer_servico') : t(`servico.${s}` as Chave))
  const tipo = (x: string) => (x === '*' ? t('regras.qualquer_tipo') : x)

  return (
    <>
      <Cabecalho
        kicker={t('quadro.kicker')}
        titulo={t('regras.titulo')}
        descricao={t('regras.descricao')}
        acoes={
          <>
            {regras.data?.pode_alterar && (
              <button type="button" className="btn sm" onClick={() => setEditando('nova')}>
                {t('regras.nova')}
              </button>
            )}
            <SeletorIdioma />
          </>
        }
      />
      {regras.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : regras.isError ? (
        <ErroDeApi erro={regras.error} />
      ) : (
        <section className="card">
          <p className="mini">{t('regras.ordem')}</p>
          <div className="tabela-rolagem">
            <table className="ng-tabela">
              <thead>
                <tr>
                  <th>{t('regras.servico')}</th>
                  <th>{t('regras.tipo')}</th>
                  <th>{t('regras.responsavel')}</th>
                  <th>{t('regras.prazo')}</th>
                  <th>{t('regras.classe')}</th>
                  <th>{t('regras.atualizada')}</th>
                  <th aria-label={t('estrutura.acoes')} />
                </tr>
              </thead>
              <tbody>
                {regras.data.regras.map((r) => (
                  <tr key={r.id}>
                    <td>{servico(r.servico)}</td>
                    <td className="mono">{tipo(r.tipo)}</td>
                    <td>
                      {r.responsavel_nome ?? <span className="mini">{t('regras.sem_responsavel')}</span>}
                      {r.automatica && (
                        <>
                          {' '}
                          <Tag>{t('regras.automatica')}</Tag>
                        </>
                      )}
                    </td>
                    <td>{r.prazo_dias == null ? '—' : t('regras.dias', { n: r.prazo_dias })}</td>
                    <td>{t(`classe.${r.classe_servico}`)}</td>
                    <td className="mini">
                      {dataHora(r.atualizado_em)} · v{r.versao}
                    </td>
                    <td>
                      {regras.data.pode_alterar && (
                        <button type="button" className="link" onClick={() => setEditando(r)}>
                          {t('comum.editar')}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {editando && regras.data && (
        <FormRegra regra={editando === 'nova' ? null : editando} pessoas={regras.data.pessoas} fechar={() => setEditando(null)} />
      )}
    </>
  )
}

function FormRegra({
  regra,
  pessoas,
  fechar,
}: {
  regra: Regra | null
  pessoas: Regras['pessoas']
  fechar: () => void
}) {
  const { t } = useT()
  const produto = useProduto().id
  const queryClient = useQueryClient()
  const [servico, setServico] = useState(regra?.servico ?? 'financeiro')
  const [tipo, setTipo] = useState(regra?.tipo ?? '')
  const [responsavel, setResponsavel] = useState(regra?.responsavel_id ?? '')
  const [prazo, setPrazo] = useState(regra?.prazo_dias?.toString() ?? '')
  const [classe, setClasse] = useState<ClasseServico>(regra?.classe_servico ?? 'standard')
  const [motivo, setMotivo] = useState('')
  const gravar = useMutation({
    mutationFn: () =>
      requisitar<Regras>(urlRegras(produto), {
        metodo: 'PUT',
        corpo: {
          servico,
          tipo: tipo.trim() || '*',
          responsavel_id: responsavel || null,
          prazo_dias: prazo === '' ? null : Number(prazo),
          classe_servico: classe,
          motivo: motivo.trim(),
        },
      }),
    onSuccess: (atualizadas) => {
      queryClient.setQueryData(chaves.regras(produto), atualizadas)
      fechar()
    },
  })
  return (
    <Janela
      titulo={regra ? t('regras.editar') : t('regras.nova')}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" type="submit" form="form-regra" disabled={motivo.trim().length < 3 || gravar.isPending}>
            {t('comum.salvar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <form
        id="form-regra"
        onSubmit={(e) => {
          e.preventDefault()
          if (motivo.trim().length >= 3) gravar.mutate()
        }}
      >
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('regras.servico')}</span>
            <select value={servico} disabled={Boolean(regra)} onChange={(e) => setServico(e.target.value)}>
              {SERVICOS.map((s) => (
                <option key={s} value={s}>
                  {s === '*' ? t('regras.qualquer_servico') : t(`servico.${s}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="campo">
            <span>{t('regras.tipo')}</span>
            <input
              value={tipo}
              disabled={Boolean(regra)}
              placeholder="*"
              pattern="^(\*|[a-z0-9_.\-]+)$"
              onChange={(e) => setTipo(e.target.value)}
            />
            <small className="mini">{t('regras.tipo_dica')}</small>
          </label>
        </div>
        <label className="campo">
          <span>{t('regras.responsavel')}</span>
          <select value={responsavel} onChange={(e) => setResponsavel(e.target.value)}>
            <option value="">{t('regras.sem_responsavel')}</option>
            {pessoas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.nome} ({p.email})
              </option>
            ))}
          </select>
        </label>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('regras.prazo')}</span>
            <input type="number" min={0} max={365} value={prazo} onChange={(e) => setPrazo(e.target.value)} />
            <small className="mini">{t('regras.prazo_dica')}</small>
          </label>
          <label className="campo">
            <span>{t('regras.classe')}</span>
            <select value={classe} onChange={(e) => setClasse(e.target.value as ClasseServico)}>
              {CLASSES.map((c) => (
                <option key={c} value={c}>
                  {t(`classe.${c}`)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="campo">
          <span>{t('regras.motivo')}</span>
          <input value={motivo} maxLength={500} required onChange={(e) => setMotivo(e.target.value)} />
          <small className="mini">{t('regras.motivo_dica')}</small>
        </label>
      </form>
      {gravar.isError && <Aviso tipo="erro">{mensagemDeErro(gravar.error, t)}</Aviso>}
    </Janela>
  )
}
