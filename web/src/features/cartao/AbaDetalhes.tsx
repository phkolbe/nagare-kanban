/**
 * Detalhes do cartao com edicao no lugar: cada campo grava ao sair dele (texto) ou ao escolher
 * (lista). O responsavel vem das pessoas `nagare.*` do produto (17/10/2026).
 */

import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useProduto } from '@pampufa/ui'

import { chaves } from '../../api/nagare'
import {
  AREAS_VALOR,
  CLASSES,
  TAMANHOS,
  TIPOS_DEMANDA,
  type CartaoDetalhe,
  type Quadro,
} from '../../api/tipos'
import { useT, type Chave } from '../../i18n'
import { usePessoas, useProjetos } from '../quadro/consultas'
import { useAcaoNoCartao } from './acoes'

type Campos = Partial<
  Pick<
    CartaoDetalhe,
    | 'title'
    | 'description'
    | 'project_id'
    | 'service_class'
    | 'assignee_id'
    | 'requester'
    | 'size'
    | 'value_area'
    | 'demand_type'
    | 'due_date'
    | 'tags'
    | 'custom_fields'
  >
>

export function AbaDetalhes({ cartao, quadro, editavel }: { cartao: CartaoDetalhe; quadro: Quadro; editavel: boolean }) {
  const { t, eu } = useT()
  const produto = useProduto().id
  const queryClient = useQueryClient()
  const projetos = useProjetos()
  const pessoas = usePessoas()
  const acao = useAcaoNoCartao<CartaoDetalhe>(cartao)
  const gravar = (campos: Campos) =>
    acao.mutate(
      { caminho: `/cards/${cartao.id}`, metodo: 'PATCH', corpo: campos },
      {
        onSuccess: (atualizado) =>
          queryClient.setQueryData<CartaoDetalhe>(chaves.cartao(produto, cartao.id), (antes) =>
            antes ? { ...antes, ...atualizado } : antes,
          ),
      },
    )
  const flags = eu?.flags ?? {}
  const prazoFaltando = cartao.service_class === 'fixed_date' && !cartao.due_date

  return (
    <div className="ng-detalhes">
      <CampoTexto rotulo="cartao.campo.titulo" valor={cartao.title} editavel={editavel} obrigatorio gravar={(v) => gravar({ title: v })} />
      <CampoTexto
        rotulo="cartao.campo.descricao"
        valor={cartao.description ?? ''}
        editavel={editavel}
        multilinha
        gravar={(v) => gravar({ description: v || null })}
      />
      <div className="ng-linha-campos">
        {flags.multi_project !== false && (
          <CampoLista
            rotulo="cartao.campo.projeto"
            valor={cartao.project_id ?? ''}
            editavel={editavel}
            opcoes={[
              { valor: '', texto: t('cartao.sem_projeto') },
              ...(projetos.data?.items ?? []).map((p) => ({ valor: p.id, texto: `${p.key} · ${p.name}` })),
            ]}
            gravar={(v) => gravar({ project_id: v || null })}
          />
        )}
        <CampoLista
          rotulo="cartao.campo.responsavel"
          valor={cartao.assignee_id ?? ''}
          editavel={editavel}
          opcoes={[
            { valor: '', texto: t('cartao.sem_responsavel') },
            ...(pessoas.data?.people ?? []).map((p) => ({ valor: p.id, texto: p.name })),
            // Quem ja estava no cartao continua visivel, mesmo fora da lista atual.
            ...(cartao.assignee_id && !(pessoas.data?.people ?? []).some((p) => p.id === cartao.assignee_id)
              ? [{ valor: cartao.assignee_id, texto: cartao.assignee_name ?? cartao.assignee_id }]
              : []),
          ]}
          gravar={(v) => gravar({ assignee_id: v || null })}
        />
      </div>
      <div className="ng-linha-campos">
        {flags.service_classes !== false && (
          <CampoLista
            rotulo="cartao.campo.classe"
            valor={cartao.service_class}
            editavel={editavel}
            opcoes={CLASSES.map((c) => ({ valor: c, texto: t(`classe.${c}`) }))}
            dica={t(`classe.${cartao.service_class}.politica`)}
            gravar={(v) => gravar({ service_class: v as CartaoDetalhe['service_class'] })}
          />
        )}
        <label className="campo">
          <span>{t('cartao.campo.prazo')}</span>
          <input
            type="date"
            disabled={!editavel}
            defaultValue={cartao.due_date?.slice(0, 10) ?? ''}
            key={cartao.due_date ?? 'sem-prazo'}
            onBlur={(e) => {
              const novo = e.target.value || null
              if (novo !== (cartao.due_date?.slice(0, 10) ?? null)) gravar({ due_date: novo })
            }}
          />
          {prazoFaltando && <small className="erro-campo">{t('cartao.prazo_faltando')}</small>}
        </label>
      </div>
      <div className="ng-linha-campos">
        <CampoLista
          rotulo="cartao.campo.tipo"
          valor={cartao.demand_type}
          editavel={editavel}
          opcoes={TIPOS_DEMANDA.map((x) => ({ valor: x, texto: t(`tipo_demanda.${x}`) }))}
          dica={t(`tipo_demanda.${cartao.demand_type}.dica`)}
          gravar={(v) => gravar({ demand_type: v as CartaoDetalhe['demand_type'] })}
        />
        <CampoLista
          rotulo="cartao.campo.area"
          valor={cartao.value_area}
          editavel={editavel}
          opcoes={AREAS_VALOR.map((x) => ({ valor: x, texto: t(`area.${x}`) }))}
          gravar={(v) => gravar({ value_area: v as CartaoDetalhe['value_area'] })}
        />
        <CampoLista
          rotulo="cartao.campo.tamanho"
          valor={cartao.size ?? ''}
          editavel={editavel}
          opcoes={[{ valor: '', texto: '—' }, ...TAMANHOS.map((x) => ({ valor: x, texto: x.toUpperCase() }))]}
          gravar={(v) => gravar({ size: (v || null) as CartaoDetalhe['size'] })}
        />
      </div>
      <CampoTexto
        rotulo="cartao.campo.solicitante"
        valor={cartao.requester ?? ''}
        editavel={editavel}
        gravar={(v) => gravar({ requester: v || null })}
      />
      <CampoTexto
        rotulo="cartao.campo.etiquetas"
        valor={cartao.tags.join(', ')}
        editavel={editavel}
        dica={t('cartao.etiquetas_dica')}
        gravar={(v) =>
          gravar({
            tags: [...new Set(v.split(',').map((x) => x.trim()).filter(Boolean))],
          })
        }
      />
      {flags.custom_fields !== false && (
        <CamposPersonalizados cartao={cartao} editavel={editavel} gravar={(campos) => gravar({ custom_fields: campos })} />
      )}
      <p className="mini">{t('cartao.quadro', { quadro: quadro.name })}</p>
    </div>
  )
}

function CampoTexto({
  rotulo,
  valor,
  editavel,
  gravar,
  multilinha = false,
  obrigatorio = false,
  dica,
}: {
  rotulo: Chave
  valor: string
  editavel: boolean
  gravar: (valor: string) => void
  multilinha?: boolean
  obrigatorio?: boolean
  dica?: string
}) {
  const { t } = useT()
  const [texto, setTexto] = useState(valor)
  useEffect(() => setTexto(valor), [valor])
  const sair = () => {
    const limpo = texto.trim()
    if (obrigatorio && !limpo) return setTexto(valor)
    if (limpo !== valor.trim()) gravar(limpo)
  }
  return (
    <label className="campo">
      <span>{t(rotulo)}</span>
      {multilinha ? (
        <textarea value={texto} rows={4} disabled={!editavel} onChange={(e) => setTexto(e.target.value)} onBlur={sair} />
      ) : (
        <input value={texto} disabled={!editavel} onChange={(e) => setTexto(e.target.value)} onBlur={sair} />
      )}
      {dica && <small className="mini">{dica}</small>}
    </label>
  )
}

function CampoLista({
  rotulo,
  valor,
  editavel,
  opcoes,
  gravar,
  dica,
}: {
  rotulo: Chave
  valor: string
  editavel: boolean
  opcoes: { valor: string; texto: string }[]
  gravar: (valor: string) => void
  dica?: string
}) {
  const { t } = useT()
  return (
    <label className="campo">
      <span>{t(rotulo)}</span>
      <select value={valor} disabled={!editavel} onChange={(e) => e.target.value !== valor && gravar(e.target.value)}>
        {opcoes.map((o) => (
          <option key={o.valor || 'vazio'} value={o.valor}>
            {o.texto}
          </option>
        ))}
      </select>
      {dica && <small className="mini">{dica}</small>}
    </label>
  )
}

/** Campos livres (chave e valor). O vinculo com a Pampufa (`pampufa`) e da integracao: so leitura. */
function CamposPersonalizados({
  cartao,
  editavel,
  gravar,
}: {
  cartao: CartaoDetalhe
  editavel: boolean
  gravar: (campos: Record<string, unknown>) => void
}) {
  const { t } = useT()
  const [nova, setNova] = useState({ chave: '', valor: '' })
  const livres = Object.entries(cartao.custom_fields ?? {}).filter(([chave]) => chave !== 'pampufa')
  const comTudo = (livresNovos: [string, unknown][]) => ({
    ...Object.fromEntries(livresNovos),
    ...(cartao.custom_fields?.pampufa ? { pampufa: cartao.custom_fields.pampufa } : {}),
  })
  return (
    <fieldset className="ng-personalizados">
      <legend>{t('cartao.campos_personalizados')}</legend>
      {livres.length === 0 && <p className="mini">{t('cartao.sem_campos')}</p>}
      {livres.map(([chave, valor]) => (
        <div key={chave} className="ng-par">
          <span className="mono">{chave}</span>
          <span>{typeof valor === 'string' ? valor : JSON.stringify(valor)}</span>
          {editavel && (
            <button type="button" className="link" onClick={() => gravar(comTudo(livres.filter(([k]) => k !== chave)))}>
              {t('comum.remover')}
            </button>
          )}
        </div>
      ))}
      {editavel && (
        <form
          className="ng-par"
          onSubmit={(e) => {
            e.preventDefault()
            const chave = nova.chave.trim()
            if (!chave || chave === 'pampufa') return
            gravar(comTudo([...livres.filter(([k]) => k !== chave), [chave, nova.valor]]))
            setNova({ chave: '', valor: '' })
          }}
        >
          <input
            placeholder={t('cartao.campo_chave')}
            aria-label={t('cartao.campo_chave')}
            value={nova.chave}
            onChange={(e) => setNova({ ...nova, chave: e.target.value })}
          />
          <input
            placeholder={t('cartao.campo_valor')}
            aria-label={t('cartao.campo_valor')}
            value={nova.valor}
            onChange={(e) => setNova({ ...nova, valor: e.target.value })}
          />
          <button className="btn sm sec" type="submit" disabled={!nova.chave.trim()}>
            {t('comum.adicionar')}
          </button>
        </form>
      )}
    </fieldset>
  )
}
