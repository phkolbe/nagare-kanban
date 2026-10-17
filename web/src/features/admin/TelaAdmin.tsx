/**
 * Administracao do Nagare sozinho (paridade com a tela antiga; na plataforma, pessoas e papeis
 * sao da Pampufa): usuarios, matriz de papeis (`rbac_advanced`), flags e planos (so o master).
 */

import { useEffect, useState } from 'react'
import { NavLink, Navigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Aviso, Cabecalho, Carregando, ErroApi, ErroDeApi, Tag, Vazio } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { nagare } from '../../api/nagare'
import type { Lista } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { Janela } from '../../componentes/Sobreposicao'
import { mensagemDeErro, useT, type Chave } from '../../i18n'
import { iniciaisDe } from '../quadro/CartaoMini'
import { travaDoUsuario } from './travas'

export const PAPEIS = ['master', 'administrator', 'editor', 'viewer', 'simulator', 'system'] as const
type Papel = (typeof PAPEIS)[number]

interface Usuario {
  id: string
  email: string
  name: string
  role: Papel
  is_active: boolean
  created_at: string | null
  last_login_at: string | null
}

interface CatalogoPapeis {
  roles: { key: Papel; capabilities: Record<string, boolean> }[]
  capabilities: string[]
  user_counts: Record<string, number>
}

interface Flag {
  key: string
  is_enabled: boolean
  min_plan: string
  effective?: boolean | null
  unlocked_by_plan?: boolean | null
}

interface ListaFlags {
  categories: { category: string; label: string; flags: Flag[]; effective_count: number }[]
  total: number
}

interface Plano {
  id: string
  name: string
  slug: string
  price_month: number
  price_year: number
  currency: string
  tier_order: number
  description: string | null
  flags: string[]
  is_active: boolean
}

interface Espaco {
  name: string
  active_plan_id: string | null
  plans: Plano[]
}

type Aba = 'usuarios' | 'papeis' | 'flags' | 'planos'

export function TelaAdmin() {
  const { aba = 'usuarios' } = useParams()
  const { t, eu } = useT()
  const { rota } = useNagare()
  const capacidades = eu?.capabilities ?? {}
  const abas: { chave: Aba; pode: boolean }[] = [
    { chave: 'usuarios', pode: Boolean(capacidades.manage_users) },
    { chave: 'papeis', pode: Boolean(capacidades.manage_users) },
    { chave: 'flags', pode: Boolean(capacidades.manage_plans_flags) },
    { chave: 'planos', pode: Boolean(capacidades.manage_plans_flags) },
  ]
  const atual = abas.find((a) => a.chave === aba)
  if (!atual) return <Navigate to={rota('admin/usuarios')} replace />
  return (
    <>
      <Cabecalho kicker={t('admin.kicker')} titulo={t(`admin.${atual.chave}` as Chave)} />
      <nav className="abas" aria-label={t('admin.kicker')}>
        {abas.map((a) =>
          a.pode ? (
            <NavLink key={a.chave} to={rota(`admin/${a.chave}`)} role="tab" aria-selected={a.chave === aba}>
              {t(`admin.${a.chave}` as Chave)}
            </NavLink>
          ) : (
            <span key={a.chave} className="ng-aba-bloqueada" title={t('menu.bloqueado_papel')} aria-disabled="true">
              🔒 {t(`admin.${a.chave}` as Chave)}
            </span>
          ),
        )}
      </nav>
      {!atual.pode ? (
        <Aviso tipo="alerta">{t('menu.bloqueado_papel')}</Aviso>
      ) : aba === 'usuarios' ? (
        <Usuarios />
      ) : aba === 'papeis' ? (
        <Papeis />
      ) : aba === 'flags' ? (
        <Flags />
      ) : (
        <Planos />
      )}
    </>
  )
}

function Usuarios() {
  const { t, eu, dataHora } = useT()
  const { produto } = useNagare()
  const queryClient = useQueryClient()
  const avisar = useAvisos()
  const [texto, setTexto] = useState('')
  const [busca, setBusca] = useState('')
  const [papel, setPapel] = useState('')
  const [editando, setEditando] = useState<Usuario | 'novo' | null>(null)
  useEffect(() => {
    const espera = window.setTimeout(() => setBusca(texto.trim()), 300)
    return () => window.clearTimeout(espera)
  }, [texto])
  const parametros = new URLSearchParams({ ...(busca ? { q: busca } : {}), ...(papel ? { role: papel } : {}) }).toString()
  const usuarios = useQuery({
    queryKey: ['nagare', produto, 'usuarios', parametros],
    queryFn: () => nagare<Lista<Usuario>>(produto, `/users${parametros ? `?${parametros}` : ''}`),
    placeholderData: (anterior) => anterior,
  })
  const catalogo = useQuery({
    queryKey: ['nagare', produto, 'papeis'],
    queryFn: () => nagare<CatalogoPapeis>(produto, '/users/roles'),
    retry: false,
  })
  const mastersAtivos =
    usuarios.data?.items.filter((u) => u.role === 'master' && u.is_active).length ?? catalogo.data?.user_counts.master ?? 1
  const alterar = useMutation({
    mutationFn: ({ id, corpo }: { id: string; corpo: Partial<Usuario> }) => nagare<Usuario>(produto, `/users/${id}`, { metodo: 'PATCH', corpo }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'usuarios'] })
      avisar(t('admin.salvo'), 'ok')
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })
  const papeisQuePode = PAPEIS.filter((p) => p !== 'system' && (p !== 'master' || eu?.role === 'master'))

  return (
    <section className="card">
      <div className="ng-barra">
        <input
          type="search"
          data-busca
          className="ng-busca"
          placeholder={t('admin.buscar')}
          aria-label={t('admin.buscar')}
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
        />
        <select aria-label={t('admin.papel')} value={papel} onChange={(e) => setPapel(e.target.value)}>
          <option value="">{t('admin.todos_papeis')}</option>
          {PAPEIS.map((p) => (
            <option key={p} value={p}>
              {t(`papel.${p}`)}
            </option>
          ))}
        </select>
        {(texto || papel) && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setTexto('')
              setPapel('')
            }}
          >
            {t('filtros.limpar')}
          </button>
        )}
        <span className="ng-espaco" />
        <button type="button" className="btn sm" onClick={() => setEditando('novo')}>
          {t('admin.novo_usuario')}
        </button>
      </div>
      {usuarios.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : usuarios.isError ? (
        <ErroDeApi erro={usuarios.error} />
      ) : usuarios.data.items.length === 0 ? (
        <Vazio titulo={t('admin.nenhum_usuario')} />
      ) : (
        <div className="tabela-rolagem">
          <table className="ng-tabela">
            <thead>
              <tr>
                <th>{t('admin.usuario')}</th>
                <th>{t('admin.papel')}</th>
                <th>{t('admin.ativo')}</th>
                <th>{t('admin.criado')}</th>
                <th>{t('admin.ultimo_acesso')}</th>
                <th aria-label={t('estrutura.acoes')} />
              </tr>
            </thead>
            <tbody>
              {usuarios.data.items.map((u) => {
                const trava = eu ? travaDoUsuario(eu, u, mastersAtivos) : 'admin.trava_voce'
                const titulo = trava ? t(trava) : undefined
                return (
                  <tr key={u.id}>
                    <td>
                      <span className="ng-avatar">{iniciaisDe(u.name)}</span> {u.name}
                      {u.id === eu?.id && <Tag cor="azul">{t('admin.voce')}</Tag>}
                      <div className="mini">{u.email}</div>
                    </td>
                    <td>
                      <select
                        value={u.role}
                        disabled={Boolean(trava) || u.role === 'system'}
                        title={titulo}
                        aria-label={t('admin.papel_de', { nome: u.name })}
                        onChange={(e) => alterar.mutate({ id: u.id, corpo: { role: e.target.value as Papel } })}
                      >
                        {(u.role === 'system' ? PAPEIS : papeisQuePode).map((p) => (
                          <option key={p} value={p}>
                            {t(`papel.${p}`)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <label className="ng-alternar" title={titulo}>
                        <input
                          type="checkbox"
                          checked={u.is_active}
                          disabled={Boolean(trava)}
                          aria-label={t('admin.ativo_de', { nome: u.name })}
                          onChange={(e) => alterar.mutate({ id: u.id, corpo: { is_active: e.target.checked } })}
                        />
                      </label>
                    </td>
                    <td className="mini">{dataHora(u.created_at)}</td>
                    <td className="mini">{u.last_login_at ? dataHora(u.last_login_at) : '—'}</td>
                    <td>
                      <button type="button" className="link" disabled={Boolean(trava) && u.id !== eu?.id} onClick={() => setEditando(u)}>
                        {t('comum.editar')}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {editando && <FormUsuario usuario={editando === 'novo' ? null : editando} papeis={papeisQuePode} fechar={() => setEditando(null)} />}
    </section>
  )
}

function FormUsuario({ usuario, papeis, fechar }: { usuario: Usuario | null; papeis: readonly Papel[]; fechar: () => void }) {
  const { t, eu } = useT()
  const { produto } = useNagare()
  const queryClient = useQueryClient()
  const [nome, setNome] = useState(usuario?.name ?? '')
  const [email, setEmail] = useState(usuario?.email ?? '')
  const [papel, setPapel] = useState<Papel>(usuario?.role ?? 'editor')
  const [ativo, setAtivo] = useState(usuario?.is_active ?? true)
  const [senha, setSenha] = useState('')
  const proprio = usuario?.id === eu?.id
  const gravar = useMutation({
    mutationFn: () => {
      const corpo: Record<string, unknown> = { name: nome.trim(), email: email.trim() }
      if (!proprio) {
        corpo.role = papel
        corpo.is_active = ativo
      }
      if (senha) corpo.password = senha
      return usuario
        ? nagare<Usuario>(produto, `/users/${usuario.id}`, { metodo: 'PATCH', corpo })
        : nagare<Usuario>(produto, '/users', { metodo: 'POST', corpo })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'usuarios'] })
      fechar()
    },
  })
  const emailTomado = gravar.error instanceof ErroApi && gravar.error.codigo === 'email_taken'
  const valido = nome.trim() && /\S+@\S+/.test(email) && (usuario ? !senha || senha.length >= 6 : senha.length >= 6)
  return (
    <Janela
      titulo={usuario ? t('admin.editar_usuario', { nome: usuario.name }) : t('admin.novo_usuario')}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" type="submit" form="form-usuario" disabled={!valido || gravar.isPending}>
            {t('comum.salvar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <form
        id="form-usuario"
        onSubmit={(e) => {
          e.preventDefault()
          if (valido) gravar.mutate()
        }}
      >
        <label className="campo">
          <span>{t('admin.nome')}</span>
          <input value={nome} maxLength={200} autoFocus required onChange={(e) => setNome(e.target.value)} />
        </label>
        <label className="campo">
          <span>{t('entrar.email')}</span>
          <input type="email" value={email} required onChange={(e) => setEmail(e.target.value)} />
          {emailTomado && <small className="erro-campo">{t('admin.email_tomado')}</small>}
        </label>
        {!proprio && (
          <div className="ng-linha-campos">
            <label className="campo">
              <span>{t('admin.papel')}</span>
              <select value={papel} onChange={(e) => setPapel(e.target.value as Papel)}>
                {papeis.map((p) => (
                  <option key={p} value={p}>
                    {t(`papel.${p}`)}
                  </option>
                ))}
              </select>
            </label>
            <label className="ng-alternar ng-alinhar-baixo">
              <input type="checkbox" checked={ativo} onChange={(e) => setAtivo(e.target.checked)} />
              {t('admin.ativo')}
            </label>
          </div>
        )}
        <label className="campo">
          <span>{t('entrar.senha')}</span>
          <input type="password" autoComplete="new-password" value={senha} onChange={(e) => setSenha(e.target.value)} />
          <small className="mini">{usuario ? t('admin.senha_manter') : t('admin.senha_minimo')}</small>
        </label>
      </form>
      {gravar.isError && !emailTomado && <Aviso tipo="erro">{mensagemDeErro(gravar.error, t)}</Aviso>}
    </Janela>
  )
}

function Papeis() {
  const { t, eu } = useT()
  const { produto } = useNagare()
  const catalogo = useQuery({
    queryKey: ['nagare', produto, 'papeis'],
    queryFn: () => nagare<CatalogoPapeis>(produto, '/users/roles'),
    retry: false,
  })
  if (catalogo.isPending) return <Carregando texto={t('comum.carregando')} />
  if (catalogo.isError) {
    const doPlano = catalogo.error instanceof ErroApi && catalogo.error.status === 403
    return doPlano ? (
      <section className="card">
        <p>{t('metricas.bloqueado', { recurso: t('flag.rbac_advanced') })}</p>
      </section>
    ) : (
      <ErroDeApi erro={catalogo.error} />
    )
  }
  const c = catalogo.data
  return (
    <section className="card">
      <div className="tabela-rolagem">
        <table className="ng-tabela ng-matriz">
          <thead>
            <tr>
              <th>{t('admin.capacidade')}</th>
              {c.roles.map((r) => (
                <th key={r.key} className={r.key === eu?.role ? 'ng-meu' : undefined}>
                  {t(`papel.${r.key}`)}
                  <div className="mini">{t('admin.pessoas', { n: c.user_counts[r.key] ?? 0 })}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {c.capabilities.map((cap) => (
              <tr key={cap}>
                <td>{t(`capacidade.${cap}` as Chave)}</td>
                {c.roles.map((r) => (
                  <td key={r.key} className={r.key === eu?.role ? 'ng-meu' : undefined}>
                    {r.capabilities[cap] ? '✓' : '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="ng-legenda-papeis">
        {PAPEIS.map((p) => (
          <div key={p}>
            <dt>{t(`papel.${p}`)}</dt>
            <dd className="mini">{t(`papel.${p}.dica`)}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function Flags() {
  const { t } = useT()
  const { produto } = useNagare()
  const queryClient = useQueryClient()
  const avisar = useAvisos()
  const flags = useQuery({ queryKey: ['nagare', produto, 'flags'], queryFn: () => nagare<ListaFlags>(produto, '/flags') })
  const alternar = useMutation({
    mutationFn: ({ chave, ligada }: { chave: string; ligada: boolean }) =>
      nagare(produto, `/flags/${chave}`, { metodo: 'PATCH', corpo: { is_enabled: ligada } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['nagare'] })
      avisar(t('admin.salvo'), 'ok')
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })
  if (flags.isPending) return <Carregando texto={t('comum.carregando')} />
  if (flags.isError) return <ErroDeApi erro={flags.error} />
  return (
    <>
      {flags.data.categories.map((cat) => (
        <section key={cat.category} className="card">
          <h2 className="ng-subtitulo">
            {t(`flag_categoria.${cat.category}` as Chave)}{' '}
            <span className="mini">{t('admin.efetivas', { n: cat.effective_count, total: cat.flags.length })}</span>
          </h2>
          <ul className="ng-flags">
            {cat.flags.map((f) => {
              const estado = f.effective ? 'efetiva' : f.unlocked_by_plan === false ? 'plano' : 'desligada'
              return (
                <li key={f.key}>
                  <label className="ng-alternar" title={f.unlocked_by_plan === false ? t('admin.flag_fora_do_plano', { plano: f.min_plan }) : undefined}>
                    <input
                      type="checkbox"
                      checked={f.is_enabled}
                      disabled={f.unlocked_by_plan === false || alternar.isPending}
                      onChange={(e) => alternar.mutate({ chave: f.key, ligada: e.target.checked })}
                    />
                    <span>
                      <b>{t(`flag.${f.key}` as Chave)}</b> <span className="mono mini">{f.key}</span>
                      <div className="mini">{t(`flag.${f.key}.dica` as Chave)}</div>
                    </span>
                  </label>
                  <Tag>{f.min_plan}</Tag>
                  <Tag cor={estado === 'efetiva' ? 'verde' : estado === 'plano' ? 'amarelo' : 'cinza'}>{t(`admin.flag_${estado}` as Chave)}</Tag>
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </>
  )
}

function Planos() {
  const { t, numero } = useT()
  const { produto } = useNagare()
  const queryClient = useQueryClient()
  const avisar = useAvisos()
  const espaco = useQuery({ queryKey: ['nagare', produto, 'espaco-planos'], queryFn: () => nagare<Espaco>(produto, '/workspace') })
  const flags = useQuery({ queryKey: ['nagare', produto, 'flags'], queryFn: () => nagare<ListaFlags>(produto, '/flags') })
  const [ativar, setAtivar] = useState<Plano | null>(null)
  const [editar, setEditar] = useState<Plano | null>(null)
  const recarregar = () => void queryClient.invalidateQueries({ queryKey: ['nagare'] })
  const definirAtivo = useMutation({
    mutationFn: (id: string) => nagare(produto, '/workspace', { metodo: 'PATCH', corpo: { active_plan_id: id } }),
    onSuccess: () => {
      recarregar()
      avisar(t('admin.plano_ativado'), 'ok')
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })
  if (espaco.isPending || flags.isPending) return <Carregando texto={t('comum.carregando')} />
  if (espaco.isError) return <ErroDeApi erro={espaco.error} />
  const todasFlags = flags.data?.categories.flatMap((c) => c.flags.map((f) => f.key)) ?? []
  const planos = [...espaco.data.plans].sort((a, b) => a.tier_order - b.tier_order)
  return (
    <>
      <div className="ng-projetos">
        {planos.map((p) => {
          const ativo = p.id === espaco.data.active_plan_id
          return (
            <article key={p.id} className={`card ng-plano${ativo ? ' ativo' : ''}`}>
              <div className="ng-projeto-topo">
                <Tag cor="ouro">{t('admin.nivel', { n: p.tier_order })}</Tag>
                {ativo && <Tag cor="verde">{t('admin.plano_ativo')}</Tag>}
                {!p.is_active && <Tag>{t('admin.aposentado')}</Tag>}
              </div>
              <h3>{p.name}</h3>
              <p className="mini">
                {p.currency} {numero(p.price_month, 0)}/{t('admin.mes')} · {p.currency} {numero(p.price_year, 0)}/{t('admin.ano')}
              </p>
              {p.description && <p className="mini">{p.description}</p>}
              <ul className="ng-flags-plano">
                {todasFlags.map((f) => (
                  <li key={f} className={p.flags.includes(f) ? 'inclui' : 'exclui'}>
                    {p.flags.includes(f) ? '✓' : '—'} {t(`flag.${f}` as Chave)}
                  </li>
                ))}
              </ul>
              <div className="acoes">
                <button type="button" className="btn sm sec" onClick={() => setEditar(p)}>
                  {t('admin.editar_flags')}
                </button>
                {!ativo && p.is_active && (
                  <button type="button" className="btn sm" onClick={() => setAtivar(p)}>
                    {t('admin.ativar')}
                  </button>
                )}
              </div>
            </article>
          )
        })}
      </div>
      {ativar && (
        <Janela
          titulo={t('admin.ativar_titulo', { plano: ativar.name })}
          fechar={() => setAtivar(null)}
          largura={460}
          rodape={
            <>
              <button
                className="btn"
                type="button"
                onClick={() => {
                  definirAtivo.mutate(ativar.id)
                  setAtivar(null)
                }}
              >
                {t('admin.ativar')}
              </button>
              <button className="btn ghost" type="button" autoFocus onClick={() => setAtivar(null)}>
                {t('comum.cancelar')}
              </button>
            </>
          }
        >
          <p>{t('admin.ativar_texto')}</p>
        </Janela>
      )}
      {editar && <FlagsDoPlano plano={editar} todas={todasFlags} fechar={() => setEditar(null)} feito={recarregar} />}
    </>
  )
}

function FlagsDoPlano({ plano, todas, fechar, feito }: { plano: Plano; todas: string[]; fechar: () => void; feito: () => void }) {
  const { t } = useT()
  const { produto } = useNagare()
  const [marcadas, setMarcadas] = useState<string[]>(plano.flags)
  const gravar = useMutation({
    mutationFn: () => nagare(produto, `/plans/${plano.id}`, { metodo: 'PATCH', corpo: { flags: marcadas } }),
    onSuccess: () => {
      feito()
      fechar()
    },
  })
  return (
    <Janela
      titulo={t('admin.flags_do_plano', { plano: plano.name })}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" type="button" disabled={gravar.isPending} onClick={() => gravar.mutate()}>
            {t('comum.salvar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <ul className="ng-flags-escolha">
        {todas.map((f) => (
          <li key={f}>
            <label className="ng-alternar">
              <input
                type="checkbox"
                checked={marcadas.includes(f)}
                onChange={(e) => setMarcadas(e.target.checked ? [...marcadas, f] : marcadas.filter((x) => x !== f))}
              />
              {t(`flag.${f}` as Chave)}
            </label>
          </li>
        ))}
      </ul>
      {gravar.isError && <Aviso tipo="erro">{mensagemDeErro(gravar.error, t)}</Aviso>}
    </Janela>
  )
}
