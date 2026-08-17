-- Nagare Kanban — full DDL (SPEC §2).
--
-- Conventions:
--   * every id is a TEXT uuid4
--   * every timestamp is an ISO-8601 UTC string ending in 'Z'
--   * booleans are INTEGER 0/1 with a CHECK
--   * every enum from the SPEC is enforced with a CHECK constraint
--
-- The whole file is idempotent (IF NOT EXISTS everywhere) so db.migrate() can be
-- run on every boot.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL
                  CHECK (role IN ('master','administrator','editor','viewer','simulator','system')),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);

-- ---------------------------------------------------------------------------
-- Boards, groups, columns
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS boards (
    id                        TEXT PRIMARY KEY,
    name                      TEXT NOT NULL,
    slug                      TEXT NOT NULL UNIQUE,
    description               TEXT,
    kind                      TEXT NOT NULL DEFAULT 'operational'
                              CHECK (kind IN ('operational','simulator')),
    source_board_id           TEXT REFERENCES boards(id) ON DELETE SET NULL,
    count_envelope_in_metrics INTEGER NOT NULL DEFAULT 0
                              CHECK (count_envelope_in_metrics IN (0,1)),
    confirm_moves             INTEGER NOT NULL DEFAULT 1 CHECK (confirm_moves IN (0,1)),
    wip_min                   INTEGER CHECK (wip_min IS NULL OR wip_min >= 0),
    wip_max                   INTEGER CHECK (wip_max IS NULL OR wip_max >= 0),
    sle_days                  INTEGER CHECK (sle_days IS NULL OR sle_days > 0),
    sle_percentile            INTEGER CHECK (sle_percentile IS NULL
                                             OR (sle_percentile > 0 AND sle_percentile <= 100)),
    created_at                TEXT NOT NULL,
    archived                  INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1))
);

CREATE INDEX IF NOT EXISTS idx_boards_kind ON boards(kind, archived);
CREATE INDEX IF NOT EXISTS idx_boards_source ON boards(source_board_id);

CREATE TABLE IF NOT EXISTS column_groups (
    id       TEXT PRIMARY KEY,
    board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    name     TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    wip_min  INTEGER CHECK (wip_min IS NULL OR wip_min >= 0),
    wip_max  INTEGER CHECK (wip_max IS NULL OR wip_max >= 0),
    color    TEXT
);

CREATE INDEX IF NOT EXISTS idx_groups_board ON column_groups(board_id, position);

CREATE TABLE IF NOT EXISTS columns (
    id                  TEXT PRIMARY KEY,
    board_id            TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    group_id            TEXT REFERENCES column_groups(id) ON DELETE SET NULL,
    name                TEXT NOT NULL,
    position            INTEGER NOT NULL DEFAULT 0,
    segment             TEXT NOT NULL CHECK (segment IN ('upstream','downstream')),
    kind                TEXT NOT NULL CHECK (kind IN ('envelope','queue','activity','done')),
    wip_min             INTEGER CHECK (wip_min IS NULL OR wip_min >= 0),
    wip_max             INTEGER CHECK (wip_max IS NULL OR wip_max >= 0),
    is_commitment_point INTEGER NOT NULL DEFAULT 0 CHECK (is_commitment_point IN (0,1)),
    is_delivery_point   INTEGER NOT NULL DEFAULT 0 CHECK (is_delivery_point IN (0,1)),
    color               TEXT,
    description         TEXT
);

CREATE INDEX IF NOT EXISTS idx_columns_board ON columns(board_id, position);
CREATE INDEX IF NOT EXISTS idx_columns_group ON columns(group_id);
CREATE INDEX IF NOT EXISTS idx_columns_segment ON columns(board_id, segment, position);

-- ---------------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    color       TEXT,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('discovery','active','on_hold','done','cancelled')),
    owner_id    TEXT REFERENCES users(id) ON DELETE SET NULL,
    started_at  TEXT,
    target_date TEXT,
    created_at  TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1))
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status, archived);

-- ---------------------------------------------------------------------------
-- Cards
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cards (
    id             TEXT PRIMARY KEY,
    board_id       TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    column_id      TEXT NOT NULL REFERENCES columns(id) ON DELETE CASCADE,
    project_id     TEXT REFERENCES projects(id) ON DELETE SET NULL,
    key            TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    description    TEXT,
    service_class  TEXT NOT NULL DEFAULT 'standard'
                   CHECK (service_class IN ('expedite','standard','fixed_date','intangible')),
    assignee_id    TEXT REFERENCES users(id) ON DELETE SET NULL,
    requester      TEXT,
    size           TEXT CHECK (size IS NULL OR size IN ('xs','s','m','l','xl')),
    value_area     TEXT NOT NULL DEFAULT 'feature'
                   CHECK (value_area IN ('feature','defect','risk','debt','compliance')),
    demand_type    TEXT NOT NULL DEFAULT 'value'
                   CHECK (demand_type IN ('value','failure','maintenance')),
    due_date       TEXT,
    position       INTEGER NOT NULL DEFAULT 0,
    envelope_state TEXT NOT NULL DEFAULT 'opened'
                   CHECK (envelope_state IN ('sealed','opened')),
    is_blocked     INTEGER NOT NULL DEFAULT 0 CHECK (is_blocked IN (0,1)),
    tags           TEXT NOT NULL DEFAULT '[]',
    custom_fields  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    opened_at      TEXT,
    committed_at   TEXT,
    delivered_at   TEXT,
    archived       INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    created_by     TEXT REFERENCES users(id) ON DELETE SET NULL
);

-- Hot query paths: the board view, the per-project roll-up and every metric that
-- scans delivered/committed timestamps.
CREATE INDEX IF NOT EXISTS idx_cards_board_column ON cards(board_id, column_id);
CREATE INDEX IF NOT EXISTS idx_cards_project ON cards(project_id);
CREATE INDEX IF NOT EXISTS idx_cards_board_archived ON cards(board_id, archived);
CREATE INDEX IF NOT EXISTS idx_cards_delivered ON cards(board_id, delivered_at);
CREATE INDEX IF NOT EXISTS idx_cards_committed ON cards(board_id, committed_at);
CREATE INDEX IF NOT EXISTS idx_cards_assignee ON cards(assignee_id);
CREATE INDEX IF NOT EXISTS idx_cards_service_class ON cards(board_id, service_class);
CREATE INDEX IF NOT EXISTS idx_cards_envelope ON cards(board_id, envelope_state);

-- ---------------------------------------------------------------------------
-- Movement history — the metrics backbone (SPEC §2.7)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS card_movements (
    id                     TEXT PRIMARY KEY,
    card_id                TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    board_id               TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    from_column_id         TEXT REFERENCES columns(id) ON DELETE SET NULL,
    to_column_id           TEXT NOT NULL REFERENCES columns(id) ON DELETE CASCADE,
    from_segment           TEXT CHECK (from_segment IS NULL
                                       OR from_segment IN ('upstream','downstream')),
    to_segment             TEXT NOT NULL CHECK (to_segment IN ('upstream','downstream')),
    moved_at               TEXT NOT NULL,
    moved_by               TEXT REFERENCES users(id) ON DELETE SET NULL,
    seconds_in_from_column INTEGER,
    note                   TEXT,
    is_backward            INTEGER NOT NULL DEFAULT 0 CHECK (is_backward IN (0,1)),
    source                 TEXT NOT NULL DEFAULT 'ui'
                           CHECK (source IN ('ui','api','mcp','seed','simulation'))
);

CREATE INDEX IF NOT EXISTS idx_movements_card ON card_movements(card_id, moved_at);
CREATE INDEX IF NOT EXISTS idx_movements_board_time ON card_movements(board_id, moved_at);
CREATE INDEX IF NOT EXISTS idx_movements_to_column ON card_movements(to_column_id, moved_at);
CREATE INDEX IF NOT EXISTS idx_movements_from_column ON card_movements(from_column_id);

-- ---------------------------------------------------------------------------
-- Audit trail
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS card_events (
    id         TEXT PRIMARY KEY,
    card_id    TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    board_id   TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    actor_id   TEXT REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL
               CHECK (event_type IN ('created','moved','updated','blocked','unblocked',
                                     'opened_envelope','archived','commented',
                                     'task_added','task_completed')),
    field      TEXT,
    old_value  TEXT,
    new_value  TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_card ON card_events(card_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_board_time ON card_events(board_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON card_events(event_type);

-- ---------------------------------------------------------------------------
-- Card detail: tasks, blockers, comments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS card_tasks (
    id           TEXT PRIMARY KEY,
    card_id      TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    is_done      INTEGER NOT NULL DEFAULT 0 CHECK (is_done IN (0,1)),
    assignee_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
    position     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_card ON card_tasks(card_id, position);

CREATE TABLE IF NOT EXISTS blockers (
    id         TEXT PRIMARY KEY,
    card_id    TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    reason     TEXT NOT NULL,
    category   TEXT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_blockers_card ON blockers(card_id, started_at);
CREATE INDEX IF NOT EXISTS idx_blockers_open ON blockers(ended_at);

CREATE TABLE IF NOT EXISTS comments (
    id         TEXT PRIMARY KEY,
    card_id    TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    author_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_card ON comments(card_id, created_at);

-- ---------------------------------------------------------------------------
-- Commercial surface: flags, plans, workspace
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_flags (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    is_enabled  INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
    min_plan    TEXT NOT NULL DEFAULT 'free'
                CHECK (min_plan IN ('free','team','business','enterprise')),
    category    TEXT NOT NULL DEFAULT 'general'
);

CREATE INDEX IF NOT EXISTS idx_flags_category ON feature_flags(category);

CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    price_month REAL NOT NULL DEFAULT 0,
    price_year  REAL NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT 'USD',
    tier_order  INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    flags       TEXT NOT NULL DEFAULT '[]',
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

CREATE INDEX IF NOT EXISTS idx_plans_tier ON plans(tier_order);

CREATE TABLE IF NOT EXISTS workspace (
    id             TEXT PRIMARY KEY CHECK (id = 'workspace'),
    name           TEXT NOT NULL,
    active_plan_id TEXT REFERENCES plans(id) ON DELETE SET NULL,
    timezone       TEXT NOT NULL DEFAULT 'UTC',
    updated_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Simulator
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS simulation_runs (
    id         TEXT PRIMARY KEY,
    board_id   TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL
               CHECK (kind IN ('monte_carlo_when','monte_carlo_how_many','wip_experiment',
                               'class_of_service_mix','replay')),
    params     TEXT NOT NULL DEFAULT '{}',
    results    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_board ON simulation_runs(board_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_kind ON simulation_runs(kind);
