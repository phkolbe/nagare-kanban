/** Contratos da API do Nagare (backend/app/schemas.py) usados pelas telas do modulo Tarefas. */

export type Segmento = 'upstream' | 'downstream'
export type SegmentoVisao = Segmento | 'unified'
export type TipoColuna = 'envelope' | 'queue' | 'activity' | 'done'
export type ClasseServico = 'expedite' | 'standard' | 'fixed_date' | 'intangible'
export type TipoDemanda = 'value' | 'failure' | 'maintenance'
export type AreaValor = 'feature' | 'defect' | 'risk' | 'debt' | 'compliance'
export type Tamanho = 'xs' | 's' | 'm' | 'l' | 'xl'
export type EstadoWip = 'under' | 'ok' | 'at_limit' | 'over'
export type CategoriaBloqueio =
  | 'dependency'
  | 'external_vendor'
  | 'waiting_on_customer'
  | 'technical'
  | 'people'
  | 'compliance'
  | 'environment'
  | 'other'
export type SituacaoProjeto = 'discovery' | 'active' | 'on_hold' | 'done' | 'cancelled'
export type Idioma = 'pt-BR' | 'en'

export const CLASSES: ClasseServico[] = ['expedite', 'fixed_date', 'standard', 'intangible']
export const TIPOS_DEMANDA: TipoDemanda[] = ['value', 'failure', 'maintenance']
export const AREAS_VALOR: AreaValor[] = ['feature', 'defect', 'risk', 'debt', 'compliance']
export const TAMANHOS: Tamanho[] = ['xs', 's', 'm', 'l', 'xl']
export const TIPOS_COLUNA: TipoColuna[] = ['envelope', 'queue', 'activity', 'done']
export const CATEGORIAS_BLOQUEIO: CategoriaBloqueio[] = [
  'dependency',
  'external_vendor',
  'waiting_on_customer',
  'technical',
  'people',
  'compliance',
  'environment',
  'other',
]
export const SITUACOES_PROJETO: SituacaoProjeto[] = ['discovery', 'active', 'on_hold', 'done', 'cancelled']

export interface Lista<T> {
  items: T[]
  total: number
}

export interface Eu {
  id: string
  email: string
  name: string
  role: string
  capabilities: Record<string, boolean>
  granted: string[]
  flags: Record<string, boolean>
  locale: Idioma
  locale_chosen: Idioma | null
  via: 'pampufa' | 'local'
  product_id: string
  /** Preferencias da tela, da pessoa (18/10/2026). */
  preferences?: { board_toolbar: FormatoFerramentas }
}

/** As ferramentas do quadro: barra recolhivel acima dele ou painel flutuante. */
export type FormatoFerramentas = 'bar' | 'panel'

export interface Grupo {
  id: string
  board_id: string
  name: string
  position: number
  wip_min: number | null
  wip_max: number | null
  color: string | null
}

export interface Coluna {
  id: string
  board_id: string
  group_id: string | null
  name: string
  position: number
  segment: Segmento
  kind: TipoColuna
  wip_min: number | null
  wip_max: number | null
  is_commitment_point: boolean
  is_delivery_point: boolean
  color: string | null
  description: string | null
}

export interface Quadro {
  id: string
  name: string
  slug: string
  description: string | null
  kind: 'operational' | 'simulator'
  confirm_moves: boolean
  count_envelope_in_metrics: boolean
  wip_min: number | null
  wip_max: number | null
  sle_days: number | null
  sle_percentile: number | null
  archived: boolean
  created_at?: string | null
  product_id: string | null
  read_only: boolean
  product_board: boolean
  flow_locked: boolean
  groups?: Grupo[] | null
  columns?: Coluna[] | null
  card_count?: number | null
}

export interface CelulaWip {
  count: number
  min: number | null
  max: number | null
  state: EstadoWip
  label: string | null
}

export interface SituacaoWip {
  board_id: string
  columns: Record<string, CelulaWip>
  groups: Record<string, CelulaWip>
  flow: CelulaWip
}

export interface Cartao {
  id: string
  board_id: string
  column_id: string
  project_id: string | null
  key: string
  title: string
  description: string | null
  service_class: ClasseServico
  assignee_id: string | null
  requester: string | null
  size: Tamanho | null
  value_area: AreaValor
  demand_type: TipoDemanda
  due_date: string | null
  position: number
  envelope_state: 'sealed' | 'opened'
  is_blocked: boolean
  tags: string[]
  custom_fields: Record<string, unknown>
  created_at: string | null
  opened_at: string | null
  committed_at: string | null
  delivered_at: string | null
  archived: boolean
  project_key: string | null
  project_color: string | null
  assignee_name: string | null
  task_total: number | null
  task_done: number | null
  age_days: number | null
  blocked_since: string | null
}

export interface VisaoQuadro {
  board: Quadro
  segment: SegmentoVisao
  groups: Grupo[]
  columns: Coluna[]
  cards: Cartao[]
  wip: SituacaoWip
  commitment_column_id: string | null
  delivery_column_id: string | null
  total_cards: number
}

export interface Tarefa {
  id: string
  card_id: string
  title: string
  is_done: boolean
  position: number
}

export interface Bloqueio {
  id: string
  reason: string
  category: CategoriaBloqueio | null
  started_at: string
  ended_at: string | null
  duration_seconds: number | null
}

export interface Comentario {
  id: string
  author_name: string | null
  body: string
  created_at: string
}

export interface Movimento {
  id: string
  from_column_id: string | null
  seconds_in_from_column: number | null
  from_column_name: string | null
  to_column_name: string | null
  moved_at: string
  moved_by_name: string | null
  note: string | null
  is_backward: boolean
}

export interface Evento {
  id: string
  actor_name: string | null
  event_type: string
  field: string | null
  old_value: string | null
  new_value: string | null
  created_at: string
}

export interface CartaoDetalhe extends Cartao {
  tasks: Tarefa[]
  blockers: Bloqueio[]
  comments: Comentario[]
  movements: Movimento[]
  column_name: string | null
  column_kind: TipoColuna | null
}

export interface Historico {
  card_id: string
  movements: Movimento[]
  events: Evento[]
}

export interface ResultadoMovimento {
  card: Cartao
  warnings: string[]
  wip: SituacaoWip | null
  committed: boolean
  delivered: boolean
}

export interface Projeto {
  id: string
  name: string
  key: string
  color: string | null
  description: string | null
  status: SituacaoProjeto
  owner_id: string | null
  owner_name: string | null
  started_at: string | null
  target_date: string | null
  archived: boolean
  card_count: number | null
  sealed_count: number | null
  opened_count: number | null
  delivered_count: number | null
  in_flight_count: number | null
}

export interface ProjetoDetalhe extends Projeto {
  demands: Record<string, number>
  cards: Cartao[]
}

export interface Pessoa {
  id: string
  name: string
  email: string
  sub: string | null
  roles: string[]
}

export interface Pessoas {
  source: 'pampufa' | 'local'
  people: Pessoa[]
}

export interface Vinculo {
  product_id: string
  link: {
    project_id: string
    board_id: string
    linked_at: string
    project: { id: string; key: string; name: string }
    board: { id: string; name: string; slug: string }
  } | null
  history: {
    id: string
    project_id: string
    board_id: string
    action: 'linked' | 'unlinked'
    reason: string | null
    moved_cards: number
    at: string
  }[]
}

export type Etapa = 'entrada' | 'a_fazer' | 'fazendo' | 'aguardando' | 'entregue'

export interface CartaoConsolidado {
  id: string
  key: string
  title: string | null
  sealed: boolean
  stage: Etapa
  column: string
  service_class: ClasseServico
  assignee_name: string | null
  due_date: string | null
  is_blocked: boolean
  created_at: string | null
  delivered_at: string | null
  tags: string[]
}

export interface Consolidado {
  stages: Etapa[]
  delivered_window_days: number
  products: {
    product_id: string
    board: { id: string; name: string }
    project: { key: string; name: string }
    counts: Record<Etapa, number>
    wip: Record<Etapa, number | null>
    cards: CartaoConsolidado[]
  }[]
}

/** Regras por tipo de providencia: moram no pampufa (NG9, pf_0008). */
export interface Regra {
  id: string
  servico: string
  tipo: string
  responsavel_id: string | null
  responsavel_nome: string | null
  prazo_dias: number | null
  classe_servico: ClasseServico
  automatica: boolean
  versao: number
  atualizado_em: string
}

export interface Regras {
  regras: Regra[]
  pessoas: { id: string; nome: string; email: string }[]
  pode_alterar: boolean
}
