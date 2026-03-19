import os
import sqlite3


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    return {r[1] for r in rows}


def main() -> None:
    if os.environ.get("DATABASE_URL"):
        raise SystemExit(
            "Refusing to reset because DATABASE_URL is set (likely production/remote DB). "
            "Unset DATABASE_URL to reset local SQLite instead."
        )

    db_path = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(__file__), "bot_data.db")
    if not os.path.exists(db_path):
        raise SystemExit(f"Local DB not found at {db_path!r}")

    conn = sqlite3.connect(db_path)
    try:
        # user_gold
        if "user_gold" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            cols = _column_names(conn, "user_gold")
            sets: list[str] = []
            if "gold" in cols:
                sets.append("gold = 0")
            for c in ("last_daily", "last_scratch_date", "last_yolo_date", "last_rob_date"):
                if c in cols:
                    sets.append(f"{c} = NULL")
            if "rob_count" in cols:
                sets.append("rob_count = 0")
            if sets:
                conn.execute(f"UPDATE user_gold SET {', '.join(sets)}")

        # user_tokens
        if "user_tokens" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            cols = _column_names(conn, "user_tokens")
            if "tokens" in cols:
                conn.execute("UPDATE user_tokens SET tokens = 0")

        # user_slots_pity
        if "user_slots_pity" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            cols = _column_names(conn, "user_slots_pity")
            sets: list[str] = []
            if "spin_count" in cols:
                sets.append("spin_count = 0")
            if "pity_trigger_at" in cols:
                sets.append("pity_trigger_at = 0")
            if sets:
                conn.execute(f"UPDATE user_slots_pity SET {', '.join(sets)}")

        conn.commit()
    finally:
        conn.close()

    print(f"Reset completed for local SQLite DB: {db_path}")


if __name__ == "__main__":
    main()

