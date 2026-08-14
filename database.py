import os
import random

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2

    class _DB:
        """
        Thin psycopg2 wrapper that mimics the sqlite3 connection API.
        Translates SQLite-style '?' placeholders to PostgreSQL '%s'.
        Auto-reconnects on dropped connections.
        """

        def __init__(self, url: str) -> None:
            self._url  = url
            self._conn = self._connect()

        def _connect(self):
            conn = psycopg2.connect(self._url)
            conn.autocommit = False
            return conn

        def execute(self, sql: str, params: tuple = ()):
            sql = sql.replace("?", "%s")
            for attempt in range(2):
                try:
                    cur = self._conn.cursor()
                    cur.execute(sql, params)
                    return cur
                except (psycopg2.OperationalError, psycopg2.InterfaceError):
                    if attempt == 0:
                        self._conn = self._connect()
                    else:
                        raise

        def commit(self) -> None:
            self._conn.commit()

        def rollback(self) -> None:
            self._conn.rollback()

    db = _DB(DATABASE_URL)
    print("[DB] Connected -> PostgreSQL (Supabase)")

else:
    import sqlite3

    DB_PATH: str = os.environ.get(
        "DB_PATH",
        os.path.join(os.path.dirname(__file__), "bot_data.db"),
    )
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    print(f"[DB] Connected -> {DB_PATH}")

db.execute("""
    CREATE TABLE IF NOT EXISTS user_gold (
        guild_id   BIGINT  NOT NULL,
        user_id    BIGINT  NOT NULL,
        gold       INTEGER NOT NULL DEFAULT 0,
        last_daily TEXT,
        PRIMARY KEY (guild_id, user_id)
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS user_tokens (
        user_id  BIGINT  PRIMARY KEY,
        tokens   INTEGER NOT NULL DEFAULT 0
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS user_slots_pity (
        guild_id       BIGINT  NOT NULL,
        user_id        BIGINT  NOT NULL,
        spin_count     INTEGER NOT NULL DEFAULT 0,
        pity_trigger_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )
""")
try:
    db.execute("ALTER TABLE user_slots_pity ADD COLUMN pity_trigger_at INTEGER DEFAULT 0")
    db.commit()
except Exception:
    pass

try:
    db.execute("ALTER TABLE user_gold ADD COLUMN last_rob_date TEXT")
    db.commit()
except Exception:
    pass

try:
    db.execute("ALTER TABLE user_gold ADD COLUMN rob_count INTEGER DEFAULT 0")
    db.commit()
except Exception:
    pass

db.commit()


def get_gold(guild_id: int, user_id: int) -> int:
    """Return the player's current Gold balance in this guild (0 for first-timers)."""
    row = db.execute(
        "SELECT gold FROM user_gold WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else 0


def add_gold(guild_id: int, user_id: int, amount: int) -> int:
    """
    Add (or subtract) Gold for a player in this guild.
    No floor — balance may go negative (debt).
    Returns the new balance.
    """
    new_val = get_gold(guild_id, user_id) + amount
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET gold = excluded.gold
        """,
        (guild_id, user_id, new_val),
    )
    db.commit()
    return new_val


def set_gold(guild_id: int, user_id: int, new_val: int) -> int:
    """
    Set Gold balance for a player in this guild.
    Returns the new balance.
    """
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET gold = excluded.gold
        """,
        (guild_id, user_id, new_val),
    )
    db.commit()
    return new_val


def get_last_daily(guild_id: int, user_id: int) -> str | None:
    row = db.execute(
        "SELECT last_daily FROM user_gold WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else None


def set_last_daily(guild_id: int, user_id: int, ts: str) -> None:
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold, last_daily) VALUES (?, ?, 0, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET last_daily = excluded.last_daily
        """,
        (guild_id, user_id, ts),
    )
    db.commit()


def get_rob_data(guild_id: int, user_id: int) -> tuple[str | None, int]:
    """
    Fetch (last_rob_date, rob_count) for a user.
    Returns (None, 0) for first-time users.

    On Postgres/Supabase, this also lazily adds the robbery columns if they
    were not created yet, so we don't crash with UndefinedColumn.
    """
    try:
        row = db.execute(
            "SELECT last_rob_date, rob_count FROM user_gold WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
    except Exception:
        try:
            try:
                db.rollback()  # type: ignore[attr-defined]
            except Exception:
                pass
            db.execute("ALTER TABLE user_gold ADD COLUMN last_rob_date TEXT")
            db.commit()
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE user_gold ADD COLUMN rob_count INTEGER DEFAULT 0")
            db.commit()
        except Exception:
            pass
        try:
            row = db.execute(
                "SELECT last_rob_date, rob_count FROM user_gold WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        except Exception:
            return None, 0

    if row is None:
        return None, 0
    return row[0], row[1] if row[1] is not None else 0


def update_rob_data(guild_id: int, user_id: int, date: str, count: int) -> None:
    """
    Update last_rob_date and rob_count for a user.
    """
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold, last_rob_date, rob_count)
        VALUES (?, ?, 0, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            last_rob_date = excluded.last_rob_date,
            rob_count = excluded.rob_count
        """,
        (guild_id, user_id, date, count),
    )
    db.commit()


def reset_rob_count_if_new_day(guild_id: int, user_id: int, today: str) -> int:
    """
    Check if last_rob_date differs from today (MYT).
    If different, reset rob_count to 0 and update date.
    Returns current rob_count.
    """
    last_rob_date, rob_count = get_rob_data(guild_id, user_id)

    if last_rob_date != today:
        update_rob_data(guild_id, user_id, today, 0)
        return 0

    return rob_count


def get_tokens(user_id: int) -> int:
    row = db.execute(
        "SELECT tokens FROM user_tokens WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else 0


def add_tokens(user_id: int, amount: int) -> None:
    new_val = get_tokens(user_id) + amount
    db.execute(
        """
        INSERT INTO user_tokens (user_id, tokens) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET tokens = excluded.tokens
        """,
        (user_id, new_val),
    )
    db.commit()


def reset_all_tokens() -> None:
    db.execute("UPDATE user_tokens SET tokens = 0")
    db.commit()


def get_slots_pity(guild_id: int, user_id: int) -> tuple[int, int]:
    """Return (spin_count, pity_trigger_at). spin_count = completed spins; pity_trigger_at = 1–5 or 0 if not set."""
    row = db.execute(
        "SELECT spin_count, pity_trigger_at FROM user_slots_pity WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    if row is None:
        return 0, 0
    return row[0], row[1] or 0


def ensure_pity_trigger_and_get(guild_id: int, user_id: int) -> tuple[int, int]:
    """
    If user is in first 5 spins and pity_trigger_at not set, set it to random 1–5 and save.
    Return (spin_count, pity_trigger_at).
    """
    spin_count, pity_trigger_at = get_slots_pity(guild_id, user_id)
    next_spin = spin_count + 1
    if next_spin <= 5 and pity_trigger_at <= 0:
        pity_trigger_at = random.randint(1, 5)
        db.execute(
            """
            INSERT INTO user_slots_pity (guild_id, user_id, spin_count, pity_trigger_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET pity_trigger_at = excluded.pity_trigger_at
            """,
            (guild_id, user_id, spin_count, pity_trigger_at),
        )
        db.commit()
    return spin_count, pity_trigger_at


def update_slots_pity_after_spin(guild_id: int, user_id: int) -> None:
    """Increment spin count after a spin."""
    spin_count, pity_trigger_at = get_slots_pity(guild_id, user_id)
    new_count = spin_count + 1
    db.execute(
        """
        INSERT INTO user_slots_pity (guild_id, user_id, spin_count, pity_trigger_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET spin_count = excluded.spin_count
        """,
        (guild_id, user_id, new_count, pity_trigger_at or 0),
    )
    db.commit()
