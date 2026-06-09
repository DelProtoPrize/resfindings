-- ============================================================================
-- schema.sql  —  Roster Portfolio & Asset Valuation  (Phase 2: data model)
-- Target: SQLite 3.24+ (canonical warehouse). Power BI reads driver-free CSV
--         extracts emitted by the ETL (Power BI has no native SQLite connector).
--
-- STAR SCHEMA
--   Fact_Roster_Historical_Value  (central fact)
--     grain: one row per  snapshot_date × league_id × roster_id × player_id
--   Dim_Leagues, Dim_Managers, Dim_Players, Dim_Draft_Picks  (conformed dims)
--   Fact_Transactions             (second fact: powers Tab 3 transaction ROI)
--   v_player_market, v_roster_assets  (helper views; see notes at bottom)
--
-- SNAPSHOT DESIGN
--   Market-value history cannot be downloaded (no free KTC/FC archive), so the
--   value fact is a SNAPSHOT fact: each ETL run appends today's values keyed by
--   snapshot_date and upserts idempotently. History accrues forward. Roster and
--   transaction history, by contrast, are fully backfilled from Sleeper.
--
-- USAGE
--   Recommended order for a clean build:
--     sqlite3 data/dynasty.db < schema.sql        -- create model (run once)
--     python etl_pipeline.py                       -- load (respects existing tables)
--   The ETL uses CREATE TABLE IF NOT EXISTS, so running this file first wins and
--   gives you the full model (FKs, audit columns, views). The views can also be
--   applied to an already-loaded db — CREATE ... IF NOT EXISTS is non-destructive.
--
-- NOTE ON FOREIGN KEYS
--   SQLite does not enforce FKs unless `PRAGMA foreign_keys = ON;` is set per
--   connection. The declarations below document the model and enable enforcement
--   if you opt in. The ETL loads dims before facts, so enforcement is safe.
-- ============================================================================

PRAGMA journal_mode = WAL;       -- better concurrent read while the ETL writes
-- PRAGMA foreign_keys = ON;     -- uncomment to enforce referential integrity

-- ----------------------------------------------------------------------------
-- DIMENSIONS
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_leagues (
    league_id           TEXT PRIMARY KEY,
    league_name         TEXT,
    season              TEXT,
    number_of_teams     INTEGER,
    is_superflex        INTEGER CHECK (is_superflex IN (0, 1)),
    te_premium_value    REAL,            -- per-reception TE bonus (0 if standard)
    ppr                 REAL,
    previous_league_id  TEXT,            -- self-reference: prior season's league
    scoring_settings_json TEXT,          -- full Sleeper scoring (for exact VBD scoring)
    roster_positions_json TEXT,          -- starter slots (for replacement levels)
    loaded_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dim_managers (
    roster_id        INTEGER NOT NULL,
    league_id        TEXT    NOT NULL,
    sleeper_user_id  TEXT,
    sleeper_username TEXT,
    owner_name       TEXT,                -- team_name if set, else display_name
    loaded_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (league_id, roster_id),   -- roster_id is only unique WITHIN a league
    FOREIGN KEY (league_id) REFERENCES dim_leagues(league_id)
);

CREATE TABLE IF NOT EXISTS dim_players (
    player_id    TEXT PRIMARY KEY,        -- Sleeper numeric player id (as text)
    player_name  TEXT,
    position     TEXT,
    age          REAL,
    nfl_team     TEXT,
    years_exp    INTEGER,
    is_rookie    INTEGER CHECK (is_rookie IN (0, 1)),
    draft_year   INTEGER,                 -- nullable; populated if available
    loaded_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dim_draft_picks (
    pick_id            TEXT PRIMARY KEY,
    league_id          TEXT,
    year               TEXT,
    round              INTEGER,
    original_owner_id  INTEGER,           -- roster that originally held the pick
    current_owner_id   INTEGER,           -- roster that holds it now
    previous_owner_id  INTEGER,
    -- Valuation columns (populated by the picks-valuation ETL step; FantasyCalc
    -- prices picks, DynastyProcess supplies an ECR tier). Nullable until then.
    pick_value_1qb     REAL,
    pick_value_2qb     REAL,
    pick_value_tier    TEXT,
    loaded_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (league_id) REFERENCES dim_leagues(league_id)
);

-- ----------------------------------------------------------------------------
-- FACTS
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact_roster_historical_value (
    snapshot_date      TEXT    NOT NULL,  -- ISO 'YYYY-MM-DD' (SQLite has no DATE type)
    league_id          TEXT    NOT NULL,
    roster_id          INTEGER NOT NULL,
    player_id          TEXT    NOT NULL,
    -- PRIMARY market source: FantasyPros ECR via DynastyProcess
    fp_value_1qb       REAL,
    fp_value_2qb       REAL,              -- Superflex value
    fp_ecr_2qb         REAL,              -- consensus rank (lower = better; for tiers)
    -- SECONDARY market source: FantasyCalc cross-check (drives arbitrage delta)
    fc_value_1qb       REAL,
    fc_value_2qb       REAL,
    sleeper_adp_value  REAL,
    fc_trend_30day     REAL,              -- 30-day movement; cheap momentum signal
    loaded_at          TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (snapshot_date, league_id, roster_id, player_id),
    FOREIGN KEY (league_id)            REFERENCES dim_leagues(league_id),
    FOREIGN KEY (player_id)            REFERENCES dim_players(player_id),
    FOREIGN KEY (league_id, roster_id) REFERENCES dim_managers(league_id, roster_id)
);

-- Second fact for Tab 3 (transaction ROI / waterfall). One row per transaction
-- "leg" (a single add or drop). Populating this is a small ETL addition that
-- loops get_transactions() across weeks and explodes adds/drops — say the word
-- and I'll wire it into Phase 1.
CREATE TABLE IF NOT EXISTS fact_transactions (
    transaction_id     TEXT    NOT NULL,
    league_id          TEXT    NOT NULL,
    week               INTEGER,
    txn_type           TEXT,              -- trade / waiver / free_agent / commissioner
    status             TEXT,
    status_updated_ms  INTEGER,           -- epoch ms from Sleeper
    txn_date           TEXT,              -- ISO date derived from status_updated_ms
    roster_id          INTEGER,
    player_id          TEXT,              -- NULL for pick-only legs
    leg                TEXT CHECK (leg IN ('add', 'drop')),
    draft_pick_season  TEXT,
    draft_pick_round   INTEGER,
    loaded_at          TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (transaction_id, roster_id, player_id, leg),
    FOREIGN KEY (league_id) REFERENCES dim_leagues(league_id)
);

-- ----------------------------------------------------------------------------
-- INDICES  (shaped for the way Tableau will filter/join)
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_fact_league_date ON fact_roster_historical_value(league_id, snapshot_date);
CREATE INDEX IF NOT EXISTS ix_fact_player      ON fact_roster_historical_value(player_id);
CREATE INDEX IF NOT EXISTS ix_fact_date        ON fact_roster_historical_value(snapshot_date);
CREATE INDEX IF NOT EXISTS ix_txn_league_date  ON fact_transactions(league_id, txn_date);
CREATE INDEX IF NOT EXISTS ix_txn_player       ON fact_transactions(player_id);
CREATE INDEX IF NOT EXISTS ix_picks_owner      ON dim_draft_picks(league_id, current_owner_id);

-- ----------------------------------------------------------------------------
-- HELPER VIEWS
--
-- v_player_market resolves each league's FORMAT (Superflex vs 1QB) in SQL so the
-- report always reads a single `fp_market_value` column instead of CASE-ing in
-- every visual. Format is static per league, so this belongs in the model.
--
-- Deliberately NOT applied here: the TE-Premium boost. That is an INTERACTIVE,
-- parameter-driven adjuster built in Power BI (a what-if parameter + DAX measure,
-- Phase 3), so it must stay out of the materialized layer. Likewise, roster
-- totals / percentile rank / HHI are left for DAX measures (Phase 3) rather than
-- pre-aggregated, so the DAX modeling skills are demonstrable in the report.
-- ----------------------------------------------------------------------------

CREATE VIEW IF NOT EXISTS v_player_market AS
SELECT
    f.snapshot_date,
    f.league_id,
    f.roster_id,
    f.player_id,
    p.player_name,
    p.position,
    p.age,
    p.nfl_team,
    l.is_superflex,
    l.te_premium_value,
    CASE WHEN l.is_superflex = 1 THEN f.fp_value_2qb ELSE f.fp_value_1qb END AS fp_market_value,
    CASE WHEN l.is_superflex = 1 THEN f.fc_value_2qb ELSE f.fc_value_1qb END AS fc_market_value,
    f.fp_ecr_2qb,
    f.fc_trend_30day,
    -- Arbitrage signal: positive => FantasyPros prices the asset above FantasyCalc
    (CASE WHEN l.is_superflex = 1 THEN f.fp_value_2qb ELSE f.fp_value_1qb END)
  - (CASE WHEN l.is_superflex = 1 THEN f.fc_value_2qb ELSE f.fc_value_1qb END) AS arb_delta_fp_minus_fc
FROM fact_roster_historical_value f
JOIN dim_leagues  l ON l.league_id  = f.league_id
LEFT JOIN dim_players p ON p.player_id = f.player_id;

-- v_roster_assets unions rostered PLAYERS with owned PICKS into one asset list so
-- Tab 2's positional allocation can treat "Draft Picks" as just another bucket.
-- Picks carry no per-day history yet, so they are pinned to the latest snapshot.
CREATE VIEW IF NOT EXISTS v_roster_assets AS
SELECT
    f.snapshot_date,
    f.league_id,
    f.roster_id,
    'PLAYER'        AS asset_type,
    f.player_id     AS asset_id,
    p.player_name   AS asset_name,
    p.position      AS position,
    CASE WHEN l.is_superflex = 1 THEN f.fp_value_2qb ELSE f.fp_value_1qb END AS fp_market_value
FROM fact_roster_historical_value f
JOIN dim_leagues  l ON l.league_id  = f.league_id
LEFT JOIN dim_players p ON p.player_id = f.player_id
UNION ALL
SELECT
    (SELECT MAX(snapshot_date) FROM fact_roster_historical_value) AS snapshot_date,
    dp.league_id,
    dp.current_owner_id AS roster_id,
    'PICK'          AS asset_type,
    dp.pick_id      AS asset_id,
    dp.year || ' R' || dp.round AS asset_name,
    'PICK'          AS position,
    CASE WHEN l.is_superflex = 1 THEN dp.pick_value_2qb ELSE dp.pick_value_1qb END AS fp_market_value
FROM dim_draft_picks dp
JOIN dim_leagues l ON l.league_id = dp.league_id;
