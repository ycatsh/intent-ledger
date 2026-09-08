CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

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
    is_system BOOLEAN NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    needs_review BOOLEAN NOT NULL DEFAULT 0,
    goal_target_cents INTEGER,
    goal_target_date DATE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_account_id) REFERENCES accounts(id) ON DELETE SET NULL
);
-- Case-insensitive uniqueness ("Groceries" and "groceries" are the same
-- account) - replaces a plain UNIQUE on name, which only caught exact-case
-- collisions.
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_name_ci ON accounts(name COLLATE NOCASE);

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
    match_type TEXT NOT NULL,
    pattern TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    payee_id INTEGER,
    priority INTEGER DEFAULT 0,
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    FOREIGN KEY (payee_id) REFERENCES payees(id)
);

CREATE TABLE IF NOT EXISTS transfer_rules (
    id INTEGER PRIMARY KEY,
    match_type TEXT NOT NULL,
    pattern TEXT NOT NULL,
    priority INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS counterparties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER,
    posted_date DATE NOT NULL,
    amount_cents INTEGER NOT NULL,
    payee_id INTEGER,
    counterparty_id INTEGER,
    raw_description TEXT NOT NULL,
    normalized_description TEXT,
    note TEXT,
    transaction_hash TEXT NOT NULL UNIQUE,
    balance_cents INTEGER,
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
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_transaction_overrides_hash ON transactions_overrides(transaction_hash);

CREATE TABLE IF NOT EXISTS transactions_splits (
    id INTEGER PRIMARY KEY,
    transaction_hash TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    note TEXT,
    FOREIGN KEY (transaction_hash) REFERENCES transactions(transaction_hash) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
CREATE INDEX IF NOT EXISTS idx_transactions_splits_hash ON transactions_splits(transaction_hash);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT,
    start_date DATE,
    end_date DATE,
    budget_cents INTEGER,
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
    period DATE NOT NULL,
    amount_cents INTEGER NOT NULL,
    PRIMARY KEY (account_id, period),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    group_id TEXT NOT NULL,
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
    amount_cents INTEGER NOT NULL,
    cadence TEXT NOT NULL,
    first_seen_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    cancelled_at DATE,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (payee_id) REFERENCES payees(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS subscriptions_charges (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER NOT NULL,
    transaction_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    posted_date DATE NOT NULL,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_charges_txn ON subscriptions_charges(transaction_id);

CREATE VIEW IF NOT EXISTS account_snapshots AS
SELECT
    account_id,
    snapshot_date,
    balance_cents
FROM (
    SELECT
        l.account_id,
        t.posted_date AS snapshot_date,
        SUM(l.amount_cents) OVER (
            PARTITION BY l.account_id
            ORDER BY t.posted_date, l.id
        ) AS balance_cents,
        ROW_NUMBER() OVER (
            PARTITION BY l.account_id, t.posted_date
            ORDER BY l.id DESC
        ) AS rn
    FROM ledger l
    JOIN transactions t ON t.id = l.transaction_id
)
WHERE rn = 1;

CREATE VIEW IF NOT EXISTS monthly_account_totals AS
SELECT
    l.account_id,
    a.name AS account_name,
    date(t.posted_date, 'start of month') AS period,
    SUM(-l.amount_cents) AS spent
FROM ledger l
JOIN transactions t ON t.id = l.transaction_id
JOIN accounts a ON a.id = l.account_id
WHERE a.type = 'expense'
GROUP BY l.account_id, a.name, period;

CREATE VIEW IF NOT EXISTS monthly_income AS
SELECT
    date(t.posted_date, 'start of month') AS period,
    SUM(CASE WHEN a.budget = 1 THEN l.amount_cents ELSE 0 END) AS income,
    SUM(l.amount_cents) AS total_income
FROM ledger l
JOIN transactions t ON t.id = l.transaction_id
JOIN accounts a ON a.id = l.account_id
WHERE a.type = 'income'
GROUP BY period;

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
INSERT OR IGNORE INTO app_settings (id, base_currency) VALUES (1, 'USD');

-- Default chart of accounts, seeded once at init time (INSERT OR IGNORE, so
-- re-running this script against an existing database is a no-op here).
INSERT OR IGNORE INTO accounts (name, type, budget, is_system, is_active) VALUES
    ('Unknown', 'expense', 0, 1, 1),
    ('Subscriptions', 'expense', 1, 1, 1),
    ('Checking', 'asset', 1, 0, 1),
    ('Savings', 'asset', 1, 0, 1),
    ('Cash', 'asset', 1, 0, 1),
    ('Credit Card XXXX', 'liability', 1, 0, 1),
    ('Groceries', 'expense', 1, 0, 1),
    ('Dining & Restaurants', 'expense', 1, 0, 1),
    ('Transportation', 'expense', 1, 0, 1),
    ('Housing', 'expense', 1, 0, 1),
    ('Utilities', 'expense', 1, 0, 1),
    ('Entertainment', 'expense', 1, 0, 1),
    ('Health & Fitness', 'expense', 1, 0, 1),
    ('Shopping', 'expense', 1, 0, 1);
