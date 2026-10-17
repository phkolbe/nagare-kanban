/**
 * Estrutura do quadro: colunas, grupos e politicas. Enquanto o fluxo nao recebeu cartao, tudo se
 * muda; depois (NG13) so nome, cor, descricao, WIP e SLE. Fluxo novo e projeto novo (NG14).
 */

import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Aviso, Carregando, ErroApi } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import { TIPOS_COLUNA, type Coluna, type Grupo, type Quadro, type Segmento, type TipoColuna } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { Janela } from '../../componentes/Sobreposicao'
import { mensagemDeErro, useT } from '../../i18n'
import { useRecarregarQuadro } from '../quadro/consultas'
import { ordenarColunas } from '../quadro/regras'

type Metodo = 'POST' | 'PATCH' | 'DELETE'

function useGravar(quadroId: string) {
  const produto = useNagare().produto
  const avisar = useAvisos()
  const { t } = useT()
  const recarregar = useRecarregarQuadro()
  return useMutation({
    mutationFn: ({ caminho, metodo, corpo }: { caminho: string; metodo: Metodo; corpo?: unknown }) =>
      nagare(produto, caminho, { metodo, corpo }),
    onSuccess: () => recarregar(quadroId),
    onError: (erro) => {
      if (!(erro instanceof ErroApi && erro.codigo === 'column_not_empty')) avisar(mensagemDeErro(erro, t), 'erro')
    },
  })
}

export function EditorEstrutura({ quadro: inicial, fechar }: { quadro: Quadro; fechar: () => void }) {
  const { t, eu } = useT()
  const produto = useNagare().produto
  const quadro = useQuery({
    queryKey: chaves.quadro(produto, inicial.id),
    queryFn: () => nagare<Quadro>(produto, `/boards/${inicial.id}`),
    initialData: inicial,
  })
  const gravar = useGravar(inicial.id)
  const [esvaziar, setEsvaziar] = useState<Coluna | null>(null)
  const q = quadro.data
  const colunas = ordenarColunas(q.columns ?? [])
  const grupos = [...(q.groups ?? [])].sort((a, b) => a.position - b.position)
  const travado = q.flow_locked || q.read_only
  const flags = eu?.flags ?? {}

  const reordenar = (indice: number, passo: -1 | 1) => {
    const ids = colunas.map((c) => c.id)
    const alvo = indice + passo
    if (alvo < 0 || alvo >= ids.length) return
    ;[ids[indice], ids[alvo]] = [ids[alvo]!, ids[indice]!]
    gravar.mutate({ caminho: `/boards/${q.id}/columns/reorder`, metodo: 'POST', corpo: { ordered_ids: ids } })
  }
  const excluir = (coluna: Coluna) =>
    gravar.mutate(
      { caminho: `/columns/${coluna.id}`, metodo: 'DELETE' },
      { onError: (erro) => erro instanceof ErroApi && erro.codigo === 'column_not_empty' && setEsvaziar(coluna) },
    )

  return (
    <Janela titulo={t('estrutura.titulo', { quadro: q.name })} fechar={fechar} largura={980}>
      {quadro.isFetching && <Carregando texto={t('comum.atualizando')} />}
      {q.read_only ? (
        <Aviso tipo="alerta">{t('quadro.aviso_so_leitura')}</Aviso>
      ) : travado ? (
        <Aviso tipo="info">{t('estrutura.travado')}</Aviso>
      ) : (
        q.product_board && <Aviso tipo="info">{t('estrutura.antes_do_primeiro')}</Aviso>
      )}

      <section className="ng-secao">
        <h3 className="ng-subtitulo">{t('estrutura.politicas')}</h3>
        <div className="ng-linha-campos">
          <CampoGravado
            rotulo={t('estrutura.nome_quadro')}
            valor={q.name}
            desabilitado={q.read_only}
            gravar={(v) => v && gravar.mutate({ caminho: `/boards/${q.id}`, metodo: 'PATCH', corpo: { name: v } })}
          />
          <CampoGravado
            rotulo={t('estrutura.sle_dias')}
            valor={q.sle_days?.toString() ?? ''}
            tipo="number"
            desabilitado={q.read_only}
            gravar={(v) => gravar.mutate({ caminho: `/boards/${q.id}`, metodo: 'PATCH', corpo: { sle_days: v ? Number(v) : null } })}
          />
          <CampoGravado
            rotulo={t('estrutura.sle_percentil')}
            valor={q.sle_percentile?.toString() ?? ''}
            tipo="number"
            desabilitado={q.read_only}
            gravar={(v) =>
              gravar.mutate({ caminho: `/boards/${q.id}`, metodo: 'PATCH', corpo: { sle_percentile: v ? Number(v) : null } })
            }
          />
        </div>
        <label className="ng-alternar">
          <input
            type="checkbox"
            checked={q.confirm_moves}
            disabled={q.read_only}
            onChange={(e) => gravar.mutate({ caminho: `/boards/${q.id}`, metodo: 'PATCH', corpo: { confirm_moves: e.target.checked } })}
          />
          {t('estrutura.confirmar_movimentos')}
        </label>
        {flags.envelope !== false && (
          <label className="ng-alternar">
            <input
              type="checkbox"
              checked={q.count_envelope_in_metrics}
              disabled={q.read_only}
              onChange={(e) =>
                gravar.mutate({ caminho: `/boards/${q.id}`, metodo: 'PATCH', corpo: { count_envelope_in_metrics: e.target.checked } })
              }
            />
            {t('estrutura.envelope_nas_metricas')}
          </label>
        )}
      </section>

      <section className="ng-secao">
        <h3 className="ng-subtitulo">{t('estrutura.colunas')}</h3>
        <div className="tabela-rolagem">
          <table className="ng-tabela-estrutura">
            <thead>
              <tr>
                <th>{t('estrutura.ordem')}</th>
                <th>{t('estrutura.nome')}</th>
                <th>{t('estrutura.segmento')}</th>
                <th>{t('estrutura.tipo')}</th>
                <th>{t('estrutura.grupo')}</th>
                <th>{t('estrutura.wip')}</th>
                <th>{t('estrutura.pontos')}</th>
                <th>{t('estrutura.cor')}</th>
                <th aria-label={t('estrutura.acoes')} />
              </tr>
            </thead>
            <tbody>
              {colunas.map((coluna, indice) => (
                <LinhaColuna
                  key={coluna.id}
                  coluna={coluna}
                  grupos={grupos}
                  travado={travado}
                  somenteLeitura={q.read_only}
                  primeira={indice === 0}
                  ultima={indice === colunas.length - 1}
                  subir={() => reordenar(indice, -1)}
                  descer={() => reordenar(indice, 1)}
                  excluir={() => excluir(coluna)}
                  gravar={(corpo) => gravar.mutate({ caminho: `/columns/${coluna.id}`, metodo: 'PATCH', corpo })}
                />
              ))}
            </tbody>
          </table>
        </div>
        {!travado && <NovaColuna quadroId={q.id} grupos={grupos} gravar={gravar.mutate} />}
      </section>

      {flags.wip_group !== false && (
        <section className="ng-secao">
          <h3 className="ng-subtitulo">{t('estrutura.grupos')}</h3>
          <p className="mini">{t('estrutura.grupos_dica')}</p>
          {grupos.length === 0 && <p className="mini">{t('estrutura.sem_grupos')}</p>}
          {grupos.map((grupo) => (
            <LinhaGrupo
              key={grupo.id}
              grupo={grupo}
              colunas={colunas}
              travado={travado}
              somenteLeitura={q.read_only}
              gravar={(corpo) => gravar.mutate({ caminho: `/groups/${grupo.id}`, metodo: 'PATCH', corpo })}
              excluir={() => gravar.mutate({ caminho: `/groups/${grupo.id}`, metodo: 'DELETE' })}
            />
          ))}
          {!travado && <NovoGrupo quadroId={q.id} colunas={colunas} gravar={gravar.mutate} />}
        </section>
      )}

      {esvaziar && (
        <EsvaziarColuna
          coluna={esvaziar}
          colunas={colunas.filter((c) => c.id !== esvaziar.id)}
          fechar={() => setEsvaziar(null)}
          esvaziar={(destino) =>
            gravar.mutate(
              { caminho: `/columns/${esvaziar.id}/drain`, metodo: 'POST', corpo: { to_column_id: destino } },
              {
                onSuccess: () => {
                  const coluna = esvaziar
                  setEsvaziar(null)
                  excluir(coluna)
                },
              },
            )
          }
        />
      )}
    </Janela>
  )
}

function CampoGravado({
  rotulo,
  valor,
  gravar,
  tipo = 'text',
  desabilitado = false,
}: {
  rotulo: string
  valor: string
  gravar: (valor: string) => void
  tipo?: 'text' | 'number' | 'color'
  desabilitado?: boolean
}) {
  return (
    <label className="campo">
      <span>{rotulo}</span>
      <input
        type={tipo}
        defaultValue={valor}
        key={valor}
        disabled={desabilitado}
        onBlur={(e) => e.target.value !== valor && gravar(e.target.value.trim())}
      />
    </label>
  )
}

function numeroOuNulo(texto: string): number | null {
  return texto === '' ? null : Math.max(0, Number(texto))
}

function LinhaColuna({
  coluna,
  grupos,
  travado,
  somenteLeitura,
  primeira,
  ultima,
  subir,
  descer,
  excluir,
  gravar,
}: {
  coluna: Coluna
  grupos: Grupo[]
  travado: boolean
  somenteLeitura: boolean
  primeira: boolean
  ultima: boolean
  subir: () => void
  descer: () => void
  excluir: () => void
  gravar: (corpo: Partial<Coluna>) => void
}) {
  const { t } = useT()
  const texto = (campo: 'name' | 'description', v: string) => {
    const limpo = v.trim()
    if (campo === 'name' && !limpo) return
    if (limpo !== (coluna[campo] ?? '')) gravar({ [campo]: limpo || null })
  }
  return (
    <tr>
      <td className="ng-ordem">
        <button type="button" className="link" disabled={travado || primeira} aria-label={t('estrutura.subir', { nome: coluna.name })} onClick={subir}>
          ▲
        </button>
        <button type="button" className="link" disabled={travado || ultima} aria-label={t('estrutura.descer', { nome: coluna.name })} onClick={descer}>
          ▼
        </button>
      </td>
      <td>
        <input
          defaultValue={coluna.name}
          key={`n-${coluna.name}`}
          aria-label={t('estrutura.nome')}
          disabled={somenteLeitura}
          onBlur={(e) => texto('name', e.target.value)}
        />
        <input
          className="mini"
          defaultValue={coluna.description ?? ''}
          key={`d-${coluna.description ?? ''}`}
          placeholder={t('estrutura.descricao')}
          aria-label={t('estrutura.descricao')}
          disabled={somenteLeitura}
          onBlur={(e) => texto('description', e.target.value)}
        />
      </td>
      <td>
        <select
          value={coluna.segment}
          disabled={travado}
          aria-label={t('estrutura.segmento')}
          onChange={(e) => gravar({ segment: e.target.value as Segmento })}
        >
          <option value="upstream">{t('segmento.upstream')}</option>
          <option value="downstream">{t('segmento.downstream')}</option>
        </select>
      </td>
      <td>
        <select
          value={coluna.kind}
          disabled={travado}
          aria-label={t('estrutura.tipo')}
          title={t(`tipo_coluna.${coluna.kind}.dica`)}
          onChange={(e) => gravar({ kind: e.target.value as TipoColuna })}
        >
          {TIPOS_COLUNA.map((k) => (
            <option key={k} value={k}>
              {t(`tipo_coluna.${k}`)}
            </option>
          ))}
        </select>
      </td>
      <td>
        <span className="mini">{grupos.find((g) => g.id === coluna.group_id)?.name ?? '—'}</span>
      </td>
      <td className="ng-wip-campos">
        <input
          type="number"
          min={0}
          defaultValue={coluna.wip_min ?? ''}
          key={`min-${coluna.wip_min}`}
          aria-label={t('wip.minimo')}
          placeholder={t('wip.minimo')}
          disabled={somenteLeitura}
          onBlur={(e) => numeroOuNulo(e.target.value) !== coluna.wip_min && gravar({ wip_min: numeroOuNulo(e.target.value) })}
        />
        <input
          type="number"
          min={0}
          defaultValue={coluna.wip_max ?? ''}
          key={`max-${coluna.wip_max}`}
          aria-label={t('wip.maximo')}
          placeholder={t('wip.maximo')}
          disabled={somenteLeitura}
          onBlur={(e) => numeroOuNulo(e.target.value) !== coluna.wip_max && gravar({ wip_max: numeroOuNulo(e.target.value) })}
        />
      </td>
      <td>
        <label className="ng-alternar mini">
          <input
            type="checkbox"
            checked={coluna.is_commitment_point}
            disabled={travado}
            onChange={(e) => gravar({ is_commitment_point: e.target.checked })}
          />
          {t('estrutura.compromisso')}
        </label>
        <label className="ng-alternar mini">
          <input
            type="checkbox"
            checked={coluna.is_delivery_point}
            disabled={travado}
            onChange={(e) => gravar({ is_delivery_point: e.target.checked })}
          />
          {t('estrutura.entrega')}
        </label>
      </td>
      <td>
        <input
          type="color"
          value={coluna.color ?? '#5f6e80'}
          disabled={somenteLeitura}
          aria-label={t('estrutura.cor')}
          onChange={(e) => gravar({ color: e.target.value })}
        />
      </td>
      <td>
        <button type="button" className="link" disabled={travado} onClick={excluir}>
          {t('comum.excluir')}
        </button>
      </td>
    </tr>
  )
}

function NovaColuna({
  quadroId,
  grupos,
  gravar,
}: {
  quadroId: string
  grupos: Grupo[]
  gravar: (args: { caminho: string; metodo: Metodo; corpo?: unknown }) => void
}) {
  const { t } = useT()
  const [nome, setNome] = useState('')
  const [segmento, setSegmento] = useState<Segmento>('downstream')
  const [tipo, setTipo] = useState<TipoColuna>('queue')
  const [grupo, setGrupo] = useState('')
  return (
    <form
      className="ng-linha-campos ng-nova"
      onSubmit={(e) => {
        e.preventDefault()
        if (!nome.trim()) return
        gravar({
          caminho: `/boards/${quadroId}/columns`,
          metodo: 'POST',
          corpo: { name: nome.trim(), segment: segmento, kind: tipo, group_id: grupo || null },
        })
        setNome('')
      }}
    >
      <label className="campo">
        <span>{t('estrutura.nova_coluna')}</span>
        <input value={nome} maxLength={120} onChange={(e) => setNome(e.target.value)} />
      </label>
      <label className="campo">
        <span>{t('estrutura.segmento')}</span>
        <select value={segmento} onChange={(e) => setSegmento(e.target.value as Segmento)}>
          <option value="upstream">{t('segmento.upstream')}</option>
          <option value="downstream">{t('segmento.downstream')}</option>
        </select>
      </label>
      <label className="campo">
        <span>{t('estrutura.tipo')}</span>
        <select value={tipo} onChange={(e) => setTipo(e.target.value as TipoColuna)}>
          {TIPOS_COLUNA.map((k) => (
            <option key={k} value={k}>
              {t(`tipo_coluna.${k}`)}
            </option>
          ))}
        </select>
      </label>
      {grupos.length > 0 && (
        <label className="campo">
          <span>{t('estrutura.grupo')}</span>
          <select value={grupo} onChange={(e) => setGrupo(e.target.value)}>
            <option value="">—</option>
            {grupos.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <button className="btn sm" type="submit" disabled={!nome.trim()}>
        {t('comum.adicionar')}
      </button>
    </form>
  )
}

function LinhaGrupo({
  grupo,
  colunas,
  travado,
  somenteLeitura,
  gravar,
  excluir,
}: {
  grupo: Grupo
  colunas: Coluna[]
  travado: boolean
  somenteLeitura: boolean
  gravar: (corpo: Record<string, unknown>) => void
  excluir: () => void
}) {
  const { t } = useT()
  const membros = colunas.filter((c) => c.group_id === grupo.id)
  return (
    <div className="ng-grupo-linha">
      <input
        defaultValue={grupo.name}
        key={grupo.name}
        aria-label={t('estrutura.nome')}
        disabled={somenteLeitura}
        onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== grupo.name && gravar({ name: e.target.value.trim() })}
      />
      <span className="mini">{membros.map((c) => c.name).join(' → ') || t('estrutura.grupo_vazio')}</span>
      <input
        type="number"
        min={0}
        defaultValue={grupo.wip_max ?? ''}
        key={`g-${grupo.wip_max}`}
        aria-label={t('wip.maximo')}
        placeholder={t('wip.maximo')}
        disabled={somenteLeitura}
        onBlur={(e) => numeroOuNulo(e.target.value) !== grupo.wip_max && gravar({ wip_max: numeroOuNulo(e.target.value) })}
      />
      <input
        type="color"
        value={grupo.color ?? '#5f6e80'}
        disabled={somenteLeitura}
        aria-label={t('estrutura.cor')}
        onChange={(e) => gravar({ color: e.target.value })}
      />
      <button type="button" className="link" disabled={travado} onClick={excluir}>
        {t('comum.excluir')}
      </button>
    </div>
  )
}

function NovoGrupo({
  quadroId,
  colunas,
  gravar,
}: {
  quadroId: string
  colunas: Coluna[]
  gravar: (args: { caminho: string; metodo: Metodo; corpo?: unknown }) => void
}) {
  const { t } = useT()
  const [nome, setNome] = useState('')
  const [de, setDe] = useState('')
  const [ate, setAte] = useState('')
  const livres = colunas.filter((c) => !c.group_id)
  const ids = colunas.map((c) => c.id)
  const span = de && ate ? ids.slice(Math.min(ids.indexOf(de), ids.indexOf(ate)), Math.max(ids.indexOf(de), ids.indexOf(ate)) + 1) : []
  return (
    <form
      className="ng-linha-campos ng-nova"
      onSubmit={(e) => {
        e.preventDefault()
        if (!nome.trim() || span.length === 0) return
        gravar({ caminho: `/boards/${quadroId}/groups`, metodo: 'POST', corpo: { name: nome.trim(), column_ids: span } })
        setNome('')
        setDe('')
        setAte('')
      }}
    >
      <label className="campo">
        <span>{t('estrutura.novo_grupo')}</span>
        <input value={nome} maxLength={120} onChange={(e) => setNome(e.target.value)} />
      </label>
      <label className="campo">
        <span>{t('estrutura.de_coluna')}</span>
        <select value={de} onChange={(e) => setDe(e.target.value)}>
          <option value="">—</option>
          {livres.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label className="campo">
        <span>{t('estrutura.ate_coluna')}</span>
        <select value={ate} onChange={(e) => setAte(e.target.value)}>
          <option value="">—</option>
          {livres.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <button className="btn sm" type="submit" disabled={!nome.trim() || span.length === 0}>
        {t('comum.adicionar')}
      </button>
    </form>
  )
}

function EsvaziarColuna({
  coluna,
  colunas,
  fechar,
  esvaziar,
}: {
  coluna: Coluna
  colunas: Coluna[]
  fechar: () => void
  esvaziar: (destino: string) => void
}) {
  const { t } = useT()
  const [destino, setDestino] = useState(colunas[0]?.id ?? '')
  return (
    <Janela
      titulo={t('estrutura.esvaziar_titulo', { nome: coluna.name })}
      fechar={fechar}
      largura={460}
      rodape={
        <>
          <button className="btn" type="button" disabled={!destino} onClick={() => esvaziar(destino)}>
            {t('estrutura.esvaziar_e_excluir')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <p>{t('estrutura.esvaziar_texto')}</p>
      <label className="campo">
        <span>{t('estrutura.esvaziar_para')}</span>
        <select value={destino} onChange={(e) => setDestino(e.target.value)}>
          {colunas.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
    </Janela>
  )
}
