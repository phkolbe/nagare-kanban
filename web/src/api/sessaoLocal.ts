/**
 * Sessao do login local (Nagare fora da plataforma). Como a tela antiga (decisao de 17/10/2026):
 * o token fica no navegador (`nagare.token`) e a sessao sobrevive a recarga ate o token vencer;
 * sem armazenamento (aba privada bloqueada), vale so na memoria desta pagina.
 */

import { ErroApi, type Opcoes } from '@pampufa/ui'

import { BASE_SOZINHO } from '../app/contexto'

const CHAVE_TOKEN = 'nagare.token'
let tokenNaMemoria: string | null = null
let aoPerderSessao: (() => void) | null = null

export function tokenLocal(): string | null {
  try {
    return window.localStorage.getItem(CHAVE_TOKEN) ?? tokenNaMemoria
  } catch {
    return tokenNaMemoria
  }
}

export function guardarToken(token: string | null): void {
  tokenNaMemoria = token
  try {
    if (token) window.localStorage.setItem(CHAVE_TOKEN, token)
    else window.localStorage.removeItem(CHAVE_TOKEN)
  } catch {
    /* sem armazenamento: so a memoria */
  }
}

export function quandoPerderSessaoLocal(callback: () => void): void {
  aoPerderSessao = callback
}

/** Quando o token vence (o `exp` do JWT, em milissegundos), ou `null` se nao der para ler. */
export function vencimentoDoToken(token: string | null): number | null {
  const parte = token?.split('.')[1]
  if (!parte) return null
  try {
    const base64 = parte.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(parte.length / 4) * 4, '=')
    const exp = (JSON.parse(atob(base64)) as { exp?: unknown }).exp
    return typeof exp === 'number' ? exp * 1000 : null
  } catch {
    return null
  }
}

/**
 * O token ja venceu (24/11/2026): a tela sabe sozinha que a sessao acabou e vai ao login, sem
 * depender de o pedido chegar ao servidor (uma falha de rede no meio mostrava "Tente de novo").
 */
export function tokenVencido(token: string | null, agora: number = Date.now()): boolean {
  const vencimento = vencimentoDoToken(token)
  return vencimento !== null && vencimento <= agora
}

/** A API no mesmo servidor do Nagare (`/api/v1`, ou `/nagare/api/v1` atras do gateway). */
export const API_LOCAL = `${BASE_SOZINHO}/api/v1`

async function problemaDe(resposta: Response) {
  try {
    const corpo = (await resposta.json()) as Record<string, unknown> & { error?: { code?: string; message?: string } }
    return {
      ...corpo,
      title: String(corpo.title ?? corpo.error?.message ?? resposta.statusText),
      status: Number(corpo.status ?? resposta.status),
      codigo: String(corpo.codigo ?? corpo.error?.code ?? 'http'),
    }
  } catch {
    return { title: resposta.statusText || 'Erro de comunicacao', status: resposta.status, codigo: 'http' }
  }
}

export async function requisitarLocal<T>(caminho: string, opcoes: Opcoes = {}): Promise<T> {
  const cabecalhos: Record<string, string> = { Accept: 'application/json', ...opcoes.cabecalhos }
  if (opcoes.corpo !== undefined) cabecalhos['Content-Type'] = 'application/json'
  const token = tokenLocal()
  if (token && tokenVencido(token)) {
    guardarToken(null)
    aoPerderSessao?.()
    throw new ErroApi({ title: 'Session expired.', status: 401, codigo: 'token_expired' } as ConstructorParameters<typeof ErroApi>[0])
  }
  if (token) cabecalhos.Authorization = `Bearer ${token}`
  const resposta = await fetch(`${API_LOCAL}${caminho}`, {
    method: opcoes.metodo ?? 'GET',
    headers: cabecalhos,
    body: opcoes.corpo === undefined ? undefined : JSON.stringify(opcoes.corpo),
  })
  if (resposta.status === 401 && token) {
    guardarToken(null)
    aoPerderSessao?.()
  }
  if (!resposta.ok) throw new ErroApi((await problemaDe(resposta)) as ConstructorParameters<typeof ErroApi>[0])
  return (resposta.status === 204 ? undefined : await resposta.json()) as T
}
