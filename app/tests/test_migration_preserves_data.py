import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic import command

APP_DIR = Path(__file__).resolve().parent.parent


def _make_legacy_db(db_path, rows):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE leads (
            id INTEGER PRIMARY KEY,
            name VARCHAR,
            company VARCHAR,
            industry VARCHAR,
            country VARCHAR,
            email VARCHAR,
            message TEXT,
            status VARCHAR,
            opened BOOLEAN
        )"""
    )
    conn.executemany(
        "INSERT INTO leads (name,company,industry,country,email,message,status,opened) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _run_migration(db_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    cfg = Config(str(APP_DIR / "alembic.ini"))
    command.stamp(cfg, "05170f950392")
    command.upgrade(cfg, "head")


def test_migration_preserves_existing_lead_data(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(
        db_path,
        [
            ("Test Person", "Acme", "IT", "Macedonia", "test@example.com", "Hello there", "sent", 0),
            ("Other Person", "Beta", "Finance", "Serbia", None, None, "waiting", 0),
        ],
    )

    _run_migration(db_path, monkeypatch)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    leads = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    assert len(leads) == 2
    assert leads[0]["name"] == "Test Person"
    assert leads[0]["email"] == "test@example.com"
    assert leads[0]["message"] == "Hello there"
    assert leads[0]["status"] == "sent"
    assert leads[1]["name"] == "Other Person"
    assert leads[1]["email"] is None

    campaigns = conn.execute("SELECT * FROM campaigns").fetchall()
    assert len(campaigns) == 1
    assert campaigns[0]["name"] == "Legacy Import"
    assert leads[0]["campaign_id"] == campaigns[0]["id"]
    assert leads[1]["campaign_id"] == campaigns[0]["id"]

    # Only the row that already had a generated message becomes a draft.
    drafts = conn.execute("SELECT * FROM drafts").fetchall()
    assert len(drafts) == 1
    assert drafts[0]["body"] == "Hello there"
    assert drafts[0]["approval_status"] == "pending"
    conn.close()


def test_migration_disambiguates_real_duplicate_legacy_emails(tmp_path, monkeypatch):
    """Reproduces the real project database: every legacy row shares the old
    `test@example.com` fallback email. The new unique (campaign_id, dedup_key)
    constraint must not drop or fail to migrate any of them."""
    db_path = tmp_path / "legacy_dupes.db"
    _make_legacy_db(
        db_path,
        [
            ("Person A", "CoA", "IT", "Macedonia", "test@example.com", "msg a", "sent", 0),
            ("Person B", "CoB", "IT", "Macedonia", "test@example.com", "msg b", "sent", 0),
            ("Person C", "CoC", "IT", "Macedonia", "test@example.com", None, "waiting", 0),
        ],
    )

    _run_migration(db_path, monkeypatch)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    leads = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    assert len(leads) == 3
    assert all(l["email"] == "test@example.com" for l in leads)

    dedup_keys = {l["dedup_key"] for l in leads}
    assert len(dedup_keys) == 3  # all disambiguated, none collapsed or dropped
    conn.close()
