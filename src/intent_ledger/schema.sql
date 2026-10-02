CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    base_currency TEXT NOT NULL DEFAULT 'USD',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    export_format TEXT NOT NULL DEFAULT 'xlsx' CHECK (export_format IN ('xlsx', 'csv'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('asset', 'liability', 'equity', 'income', 'expense')),
    parent_account_id INTEGER,
    institution TEXT,
    account_number_last4 TEXT,
    budget BOOLEAN NOT NULL DEFAULT 0,
    role TEXT CHECK (role IN ('unknown', 'subscriptions')),
    is_active BOOLEAN NOT NULL DEFAULT 1,
    needs_review BOOLEAN NOT NULL DEFAULT 0,
    default_parser_slug TEXT,
    goal_target_cents INTEGER,
    goal_target_date DATE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_account_id) REFERENCES accounts(id) ON DELETE SET NULL
);
-- Case-insensitive uniqueness ("Groceries" and "groceries" are the same
-- account) - replaces a plain UNIQUE on name, which only caught exact-case
-- collisions.
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_name_ci ON accounts(name COLLATE NOCASE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_role ON accounts(role) WHERE role IS NOT NULL;

CREATE TABLE IF NOT EXISTS payees (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL,
    account_id INTEGER,
    is_system BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS payee_aliases (
    id INTEGER PRIMARY KEY,
    payee_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source TEXT NOT NULL,
    usage_count INTEGER DEFAULT 1,
    last_seen DATE,
    FOREIGN KEY (payee_id) REFERENCES payees(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_unique ON payee_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS account_rules (
    id INTEGER PRIMARY KEY,
    match_type TEXT NOT NULL CHECK (match_type IN ('contains', 'prefix', 'suffix', 'equals', 'regex')),
    pattern TEXT NOT NULL CHECK (pattern != ''),
    account_id INTEGER NOT NULL,
    payee_id INTEGER,
    priority INTEGER DEFAULT 0,
    needs_review BOOLEAN NOT NULL DEFAULT 0,
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    FOREIGN KEY (payee_id) REFERENCES payees(id)
);

CREATE TABLE IF NOT EXISTS transfer_rules (
    id INTEGER PRIMARY KEY,
    match_type TEXT NOT NULL CHECK (match_type IN ('contains', 'prefix', 'suffix', 'equals', 'regex')),
    pattern TEXT NOT NULL CHECK (pattern != ''),
    priority INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS counterparties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    posted_date DATE NOT NULL CHECK (date(posted_date) IS posted_date),
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer'),
    payee_id INTEGER,
    counterparty_id INTEGER,
    raw_description TEXT NOT NULL,
    normalized_description TEXT,
    note TEXT,
    transaction_hash TEXT NOT NULL UNIQUE,
    balance_cents INTEGER CHECK (balance_cents IS NULL OR typeof(balance_cents) = 'integer'),
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'bank' CHECK (status IN ('bank', 'manual')),
    FOREIGN KEY (payee_id) REFERENCES payees(id),
    FOREIGN KEY (counterparty_id) REFERENCES counterparties(id) ON DELETE SET NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
CREATE INDEX IF NOT EXISTS idx_txn_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_txn_payee ON transactions(payee_id);
CREATE INDEX IF NOT EXISTS idx_txn_counterparty ON transactions(counterparty_id);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(posted_date);

CREATE TABLE IF NOT EXISTS transactions_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_hash TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    FOREIGN KEY (transaction_hash) REFERENCES transactions(transaction_hash) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_transaction_overrides_hash ON transactions_overrides(transaction_hash);

CREATE TABLE IF NOT EXISTS transactions_splits (
    id INTEGER PRIMARY KEY,
    transaction_hash TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer'),
    note TEXT,
    FOREIGN KEY (transaction_hash) REFERENCES transactions(transaction_hash) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
CREATE INDEX IF NOT EXISTS idx_transactions_splits_hash ON transactions_splits(transaction_hash);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT,
    start_date DATE CHECK (start_date IS NULL OR date(start_date) IS start_date),
    end_date DATE CHECK (end_date IS NULL OR date(end_date) IS end_date),
    budget_cents INTEGER CHECK (budget_cents IS NULL OR (typeof(budget_cents) = 'integer' AND budget_cents >= 0)),
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    archived BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions_projects (
    id INTEGER PRIMARY KEY,
    transaction_hash TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    UNIQUE (transaction_hash, project_id),
    FOREIGN KEY (transaction_hash) REFERENCES transactions(transaction_hash) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS budgets (
    account_id INTEGER NOT NULL,
    period DATE NOT NULL CHECK (date(period, 'start of month') IS period),
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents >= 0),
    PRIMARY KEY (account_id, period),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL,
    transaction_id INTEGER,
    account_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger(account_id);
CREATE INDEX IF NOT EXISTS idx_ledger_transaction ON ledger(transaction_id);
CREATE INDEX IF NOT EXISTS idx_ledger_group ON ledger(group_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    payee_id INTEGER NOT NULL,
    account_id INTEGER,
    name TEXT,
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents < 0),
    cadence TEXT NOT NULL CHECK (cadence IN ('weekly', 'monthly', 'quarterly', 'yearly')),
    first_seen_date DATE NOT NULL CHECK (date(first_seen_date) IS first_seen_date),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    cancelled_at DATE CHECK (cancelled_at IS NULL OR date(cancelled_at) IS cancelled_at),
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (payee_id) REFERENCES payees(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE VIEW IF NOT EXISTS ledger_running_balance AS
SELECT
    l.id AS ledger_id,
    l.account_id,
    t.transaction_hash,
    t.posted_date,
    SUM(l.amount_cents) OVER (PARTITION BY l.account_id ORDER BY t.posted_date, l.id) AS balance_cents
FROM ledger l
JOIN transactions t ON t.id = l.transaction_id;

CREATE VIEW IF NOT EXISTS account_snapshots AS
SELECT account_id, snapshot_date, balance_cents
FROM (
    SELECT
        account_id,
        posted_date AS snapshot_date,
        balance_cents,
        ROW_NUMBER() OVER (PARTITION BY account_id, posted_date ORDER BY ledger_id DESC) AS rn
    FROM ledger_running_balance
)
WHERE rn = 1;

CREATE VIEW IF NOT EXISTS income_expense_lines AS
SELECT
    l.id AS line_id,
    l.group_id,
    l.transaction_id,
    t.transaction_hash,
    t.posted_date,
    date(t.posted_date, 'start of month') AS period,
    t.payee_id,
    l.account_id,
    a.type AS account_type,
    a.budget AS account_budget,
    CASE WHEN a.type = 'income' THEN -l.amount_cents ELSE l.amount_cents END AS amount_cents
FROM ledger l
JOIN accounts a ON a.id = l.account_id
JOIN transactions t ON t.id = l.transaction_id
WHERE a.type IN ('income', 'expense');

CREATE VIEW IF NOT EXISTS money_flows AS
SELECT
    l.group_id,
    t.transaction_hash,
    t.posted_date,
    SUM(CASE WHEN a.type IN ('asset', 'liability') THEN l.amount_cents ELSE 0 END) AS amount_cents
FROM ledger l
JOIN accounts a ON a.id = l.account_id
JOIN transactions t ON t.id = l.group_id
GROUP BY l.group_id;

CREATE VIEW IF NOT EXISTS subscriptions_charges AS
SELECT DISTINCT
    s.id AS subscription_id,
    t.id AS transaction_id,
    -l.amount_cents AS amount_cents,
    t.posted_date
FROM subscriptions s
JOIN transactions t
    ON t.payee_id = s.payee_id
   AND (s.cancelled_at IS NULL OR t.posted_date <= s.cancelled_at)
JOIN ledger l
    ON l.transaction_id = t.id
   AND l.amount_cents = -s.amount_cents
JOIN accounts a
    ON a.id = l.account_id
   AND a.type = 'expense'
WHERE NOT EXISTS (
    SELECT 1
    FROM subscriptions earlier
    WHERE earlier.payee_id = s.payee_id
      AND earlier.amount_cents = s.amount_cents
      AND earlier.id < s.id
      AND (earlier.cancelled_at IS NULL OR t.posted_date <= earlier.cancelled_at)
);

INSERT OR IGNORE INTO app_settings (id, base_currency) VALUES (1, 'USD');

-- Default chart of accounts, seeded once at init time (INSERT OR IGNORE, so
-- re-running this script against an existing database is a no-op here).
INSERT OR IGNORE INTO accounts (name, type, budget, role, is_active) VALUES
    ('Unknown', 'expense', 0, 'unknown', 1),
    ('Subscriptions', 'expense', 1, 'subscriptions', 1),
    ('Checking', 'asset', 1, NULL, 1),
    ('Savings', 'asset', 1, NULL, 1),
    ('Cash', 'asset', 1, NULL, 1),
    ('Credit Card XXXX', 'liability', 1, NULL, 1),
    ('Groceries', 'expense', 1, NULL, 1),
    ('Dining & Restaurants', 'expense', 1, NULL, 1),
    ('Transportation', 'expense', 1, NULL, 1),
    ('Housing', 'expense', 1, NULL, 1),
    ('Utilities', 'expense', 1, NULL, 1),
    ('Entertainment', 'expense', 1, NULL, 1),
    ('Health & Fitness', 'expense', 1, NULL, 1),
    ('Shopping', 'expense', 1, NULL, 1);
