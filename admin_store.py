"""Admin database store — PostgreSQL (psycopg2), thread-safe, parameterized."""
import secrets, os, json, time, hashlib, re
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Lock
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from psycopg2.extras import DictCursor

_lock         = Lock()
_typing       = {}
SESSION_TTL_H = 8
LOCKOUT_AFTER = 6
LOCKOUT_MIN   = 15

ROLES      = ['owner', 'admin', 'moderator', 'helper']
ROLE_LEVEL = {r: i for i, r in enumerate(ROLES)}

PERMISSIONS = {
    'owner':     {'all', 'manage_admins', 'gateway', 'batch_config', 'debug', 'debug_resolve'},
    'admin':     {'manage_admins', 'batch_config', 'debug', 'debug_resolve'},  # gateway is controlled per user below
    'moderator': {'batch_config', 'debug', 'debug_resolve'},
    'helper':    {'debug_resolve', 'debug'},
}

ROLE_META = {
    'owner':     {'label': 'Owner',     'color': 'danger'},
    'admin':     {'label': 'Admin',     'color': 'primary'},
    'moderator': {'label': 'Moderador', 'color': 'warning'},
    'helper':    {'label': 'Ajudante',  'color': 'secondary'},
}

AVATARS = [f'av{i}' for i in range(1, 9)] + [f'av{i}f' for i in range(1, 9)]

ROLE_AVATAR_MAP = {
    'owner':     ['av5',  'av5f'],
    'admin':     ['av3',  'av3f'],
    'moderator': ['av2',  'av2f'],
    'helper':    ['av4',  'av4f'],
    'support':   ['av1',  'av1f'],
}
_ROLE_AVATAR_DEFAULT = ['av1', 'av1f']

STATUSES = {
    'online':    {'label': 'Disponível',  'color': '#22c55e', 'icon': 'fa-circle'},
    'busy':      {'label': 'Ocupado',     'color': '#f59e0b', 'icon': 'fa-circle'},
    'invisible': {'label': 'Invisível',   'color': '#6b7280', 'icon': 'fa-circle-dot'},
    'offline':   {'label': 'Offline',     'color': '#374151', 'icon': 'fa-circle'},
}


class _DBWrapper:
    """Thin wrapper that exposes a psycopg2 DictCursor as if it were a sqlite3 connection."""
    def __init__(self, conn, cur):
        self._conn = conn
        self._cur  = cur

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self._cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


@contextmanager
def _conn():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    conn = psycopg2.connect(db_url, cursor_factory=DictCursor)
    cur  = conn.cursor()
    db   = _DBWrapper(conn, cur)
    try:
        yield db
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _now(dt=None):
    return (dt or datetime.now()).isoformat(timespec='seconds')


def fmt_ts(ts: str) -> str:
    if not ts:
        return '—'
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt.strftime('%d/%m/%Y %H:%M:%S')
    except Exception:
        return str(ts)


def init_db():
    with _lock, _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id           SERIAL PRIMARY KEY,
            username     TEXT UNIQUE NOT NULL,
            password     TEXT NOT NULL,
            role         TEXT NOT NULL DEFAULT 'helper',
            avatar       TEXT NOT NULL DEFAULT 'av1',
            display_name TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            created_by   TEXT,
            active       INTEGER NOT NULL DEFAULT 1,
            status       TEXT NOT NULL DEFAULT 'offline',
            crowned      INTEGER NOT NULL DEFAULT 0,
            access_dev_mode BOOLEAN NOT NULL DEFAULT FALSE,
            manage_gateways BOOLEAN NOT NULL DEFAULT FALSE
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            admin_id   INTEGER NOT NULL,
            ip         TEXT,
            created_at TEXT NOT NULL,
            last_seen  TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id          SERIAL PRIMARY KEY,
            admin_id    INTEGER,
            admin_name  TEXT NOT NULL,
            role        TEXT NOT NULL,
            action      TEXT NOT NULL,
            detail      TEXT,
            ip          TEXT,
            ts          TEXT NOT NULL,
            hidden_by   INTEGER DEFAULT NULL,
            hidden_at   TEXT DEFAULT NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id         SERIAL PRIMARY KEY,
            admin_id   INTEGER,
            admin_name TEXT NOT NULL,
            role       TEXT NOT NULL,
            action     TEXT NOT NULL,
            detail     TEXT,
            before_val TEXT,
            after_val  TEXT,
            ip         TEXT,
            ts         TEXT NOT NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS ip_lockouts (
            ip           TEXT PRIMARY KEY,
            failures     INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id         SERIAL PRIMARY KEY,
            type       TEXT NOT NULL,
            message    TEXT NOT NULL,
            ts         TEXT NOT NULL,
            read_by    TEXT NOT NULL DEFAULT '[]',
            min_role   TEXT NOT NULL DEFAULT 'admin'
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id          SERIAL PRIMARY KEY,
            from_id     INTEGER NOT NULL,
            from_name   TEXT NOT NULL,
            from_role   TEXT NOT NULL,
            to_role     TEXT NOT NULL,
            message     TEXT NOT NULL,
            ts          TEXT NOT NULL,
            resolved    INTEGER NOT NULL DEFAULT 0,
            resolved_by TEXT,
            resolved_at TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_mode (
            id       INTEGER PRIMARY KEY DEFAULT 1,
            enabled  INTEGER NOT NULL DEFAULT 0,
            message  TEXT NOT NULL DEFAULT 'Site em manutenção. Voltamos em breve.',
            set_by   TEXT,
            set_at   TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS gateway_backups (
            id       SERIAL PRIMARY KEY,
            config   TEXT NOT NULL,
            saved_by TEXT NOT NULL,
            ts       TEXT NOT NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS gateway_change_requests (
            id            SERIAL PRIMARY KEY,
            admin_id      INTEGER NOT NULL,
            admin_name    TEXT NOT NULL,
            admin_role    TEXT NOT NULL,
            scope         TEXT NOT NULL,
            before_cfg    TEXT NOT NULL,
            proposed_cfg  TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            created_at    TEXT NOT NULL,
            decided_at    TEXT,
            decided_by    TEXT,
            decision_note TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS pix_payments (
            id         SERIAL PRIMARY KEY,
            payment_id TEXT NOT NULL,
            gateway_id TEXT NOT NULL,
            amount     REAL NOT NULL,
            currency   TEXT NOT NULL DEFAULT 'BRL',
            status     TEXT NOT NULL DEFAULT 'pending',
            ts         TEXT NOT NULL,
            expires_at TEXT,
            updated_at TEXT,
            updated_by TEXT,
            font_id    TEXT NOT NULL DEFAULT 'default',
            text_effect TEXT NOT NULL DEFAULT 'solid',
            name_color TEXT NOT NULL DEFAULT '#f3f4f6'
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            url        TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 1,
            events     TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS suspicious_requests (
            id               SERIAL PRIMARY KEY,
            ip               TEXT NOT NULL,
            attack_type      TEXT NOT NULL,
            url_path         TEXT NOT NULL,
            method           TEXT NOT NULL DEFAULT 'GET',
            ts               TEXT NOT NULL,
            count            INTEGER NOT NULL DEFAULT 1,
            ua               TEXT,
            browser_name     TEXT,
            operating_system TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS lockout_unlock_requests (
            id          SERIAL PRIMARY KEY,
            ip          TEXT NOT NULL,
            ua          TEXT,
            geo         TEXT,
            ts          TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            handled_by  TEXT,
            handled_at  TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS brute_force_incidents (
            id                SERIAL PRIMARY KEY,
            ip                TEXT NOT NULL,
            username_tried    TEXT,
            failures          INTEGER NOT NULL DEFAULT 0,
            ts                TEXT NOT NULL,
            ua                TEXT,
            geo_city          TEXT,
            geo_region        TEXT,
            geo_country       TEXT,
            geo_lat           TEXT,
            geo_lon           TEXT,
            geo_isp           TEXT,
            geo_asn           TEXT,
            geo_is_proxy      BOOLEAN NOT NULL DEFAULT FALSE,
            geo_is_hosting    BOOLEAN NOT NULL DEFAULT FALSE,
            hardware_json     TEXT,
            canvas_fp         TEXT,
            webrtc_ip         TEXT,
            browser_name      TEXT,
            operating_system  TEXT,
            hardware_ram      TEXT,
            hardware_gpu      TEXT,
            hardware_cores    TEXT,
            screen_resolution TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS team_chat (
            id         SERIAL PRIMARY KEY,
            admin_id   INTEGER NOT NULL,
            admin_name TEXT NOT NULL,
            role       TEXT NOT NULL,
            avatar     TEXT NOT NULL DEFAULT 'av1',
            message    TEXT NOT NULL,
            ts         TEXT NOT NULL,
            channel    TEXT NOT NULL DEFAULT 'team',
            room_id    INTEGER DEFAULT NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id         SERIAL PRIMARY KEY,
            p1_id      INTEGER NOT NULL,
            p1_name    TEXT NOT NULL,
            p2_id      INTEGER NOT NULL,
            p2_name    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            closed     INTEGER NOT NULL DEFAULT 0,
            closed_at  TEXT,
            closed_by  TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS team_chat_seen (
            key          TEXT NOT NULL,
            admin_id     INTEGER NOT NULL,
            last_seen_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (key, admin_id)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS donation_ranking (
            id            SERIAL PRIMARY KEY,
            nickname      TEXT NOT NULL,
            avatar_id     TEXT NOT NULL DEFAULT 'm_1',
            total_donated NUMERIC NOT NULL DEFAULT 0,
            currency      TEXT NOT NULL DEFAULT 'BRL',
            vip_level     INTEGER NOT NULL DEFAULT 5,
            secret_token  TEXT UNIQUE NOT NULL,
            ts            TEXT NOT NULL,
            font_id       TEXT NOT NULL DEFAULT 'default',
            text_effect   TEXT NOT NULL DEFAULT 'solid',
            name_color    TEXT NOT NULL DEFAULT '#f3f4f6',
            last_seen_at  DOUBLE PRECISION
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS vip_tiers_config (
            tier_id    INTEGER PRIMARY KEY,
            tier_name  TEXT    NOT NULL,
            min_value  NUMERIC NOT NULL DEFAULT 0,
            bonus_urls INTEGER NOT NULL DEFAULT 0
        )
        """)

        # Persistent dashboard counters + admin-set overrides — replaces
        # dashboard_config.json so metrics survive container reboots.
        c.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_metrics (
            metric_key   TEXT PRIMARY KEY,
            metric_value DOUBLE PRECISION NOT NULL DEFAULT 0
        )
        """)

        # ── WAF auto-ban list ─────────────────────────────────────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS waf_bans (
            id          SERIAL PRIMARY KEY,
            ip          TEXT NOT NULL UNIQUE,
            reason      TEXT NOT NULL DEFAULT '',
            url_path    TEXT NOT NULL DEFAULT '',
            method      TEXT NOT NULL DEFAULT 'GET',
            ua          TEXT,
            ts          TEXT NOT NULL,
            unban_ts    TEXT
        )
        """)

        # ── WAF: first-strike warning table (two-strikes system) ─────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS waf_warnings (
            ip          TEXT PRIMARY KEY,
            count       INT  NOT NULL DEFAULT 1,
            reason      TEXT NOT NULL DEFAULT '',
            url_path    TEXT NOT NULL DEFAULT '',
            method      TEXT NOT NULL DEFAULT 'GET',
            ua          TEXT,
            ts          TEXT NOT NULL
        )
        """)

        # ── Firewall: whitelist for devs / pentesters (ADD02/ADD03) ─────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS waf_lista_branca (
            id              SERIAL PRIMARY KEY,
            nome_dev        TEXT NOT NULL DEFAULT '',
            ip              TEXT,
            dispositivo_id  TEXT,
            modo_teste_ativo BOOLEAN NOT NULL DEFAULT FALSE,
            ultimo_login    TEXT,
            criado_em       TEXT NOT NULL
        )
        """)

        # Unique index so the same device fingerprint can't be added twice
        c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uix_waf_lista_branca_dev_id
        ON waf_lista_branca (dispositivo_id)
        WHERE dispositivo_id IS NOT NULL AND dispositivo_id <> ''
        """)

        # ── WAF: seed known-malicious IPs with their historical attack records ──
        _WAF_SEEDS = [
            ('20.125.48.163',
             'Varredura de Path Traversal — Tentativa de acesso não autorizado a diretórios e arquivos confidenciais do sistema',
             '/..%2f..%2f..%2fetc%2fpasswd', 'GET'),
            ('52.240.186.21',
             'Exfiltração de Credenciais — Acesso forçado a arquivos de configuração sensíveis (.env / segredos do sistema)',
             '/.env.production', 'GET'),
        ]
        for _ip, _reason, _url, _method in _WAF_SEEDS:
            c.execute("""
                INSERT INTO waf_bans (ip, reason, url_path, method, ua, ts)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (ip) DO NOTHING
            """, (_ip, _reason, _url, _method, '[Histórico — registrado manualmente]',
                  '2026-08-03T00:00:00'))

        # Failed/blocked scrape URLs — replaces failed_urls.json so the
        # debug panel state survives container reboots.
        c.execute("""
        CREATE TABLE IF NOT EXISTS failed_urls (
            url         TEXT PRIMARY KEY,
            domain      TEXT NOT NULL DEFAULT '',
            error       TEXT NOT NULL DEFAULT '',
            error_type  TEXT NOT NULL DEFAULT 'unknown',
            first_seen  TEXT NOT NULL DEFAULT '',
            last_seen   TEXT NOT NULL DEFAULT '',
            count       INTEGER NOT NULL DEFAULT 1,
            resolved    BOOLEAN NOT NULL DEFAULT FALSE
        )
        """)
        # Seed default VIP tiers if empty (VIP SUPREME=0 highest, VIP 5 = base)
        c.execute("SELECT COUNT(*) AS n FROM vip_tiers_config")
        if (c.fetchone()['n'] or 0) == 0:
            for _td, _tn, _mv, _bu in [
                (0, 'VIP SUPREME 🔥', 500, 150),
                (1, 'VIP 1 💎',       100, 100),
                (2, 'VIP 2',           60,  60),
                (3, 'VIP 3',           35,  40),
                (4, 'VIP 4',           15,  20),
                (5, 'VIP 5',            5,  10),
            ]:
                c.execute(
                    "INSERT INTO vip_tiers_config(tier_id, tier_name, min_value, bonus_urls) VALUES(%s,%s,%s,%s)",
                    (_td, _tn, _mv, _bu)
                )
        # Ensure VIP SUPREME row exists on pre-existing installations
        c.execute(
            "INSERT INTO vip_tiers_config(tier_id, tier_name, min_value, bonus_urls) "
            "VALUES(0,'VIP SUPREME \U0001f525',500,150) ON CONFLICT(tier_id) DO NOTHING"
        )

        _migrate_columns(c)

        c.execute("SELECT COUNT(*) AS n FROM admins")
        count = c.fetchone()['n'] or 0
        if count == 0:
            _seed_owner(c)
        # Owners always retain the administrative controls. This also covers
        # an owner inserted by a previous installation before these columns
        # existed.
        c.execute(
            "UPDATE admins SET access_dev_mode=TRUE, manage_gateways=TRUE "
            "WHERE role='owner'"
        )

        c.execute(
            "INSERT INTO maintenance_mode(id, enabled) VALUES(1, 0) ON CONFLICT DO NOTHING"
        )


# ── Donation Ranking ───────────────────────────────────────────────────────────

def _compute_vip_level_with_conn(c, total_donated: float) -> int:
    """Compute VIP level from DB tiers config using an open cursor.
    VIP SUPREME (0) is the god-tier; VIP 5 is the default fallback.
    Uses >= so that exact threshold donations are correctly assigned.
    tier_id=0 (SUPREME, 500) comes first in ASC order naturally."""
    try:
        c.execute(
            "SELECT tier_id, min_value FROM vip_tiers_config "
            "WHERE tier_id < 5 ORDER BY tier_id ASC"
        )
        for tier in c.fetchall():
            if total_donated >= float(tier['min_value']):
                return int(tier['tier_id'])
    except Exception:
        # Fallback to hardcoded thresholds if table not yet available
        if total_donated >= 500: return 0
        if total_donated >= 100: return 1
        if total_donated >= 60:  return 2
        if total_donated >= 35:  return 3
        if total_donated >= 15:  return 4
    return 5


def _compute_vip_level(total_donated: float) -> int:
    """Standalone VIP level computation — opens its own connection."""
    with _conn() as c:
        return _compute_vip_level_with_conn(c, total_donated)


def get_vip_tiers() -> list:
    """Return all VIP tier configs ordered by tier_id ASC."""
    with _conn() as c:
        c.execute("SELECT tier_id, tier_name, min_value, bonus_urls FROM vip_tiers_config ORDER BY tier_id ASC")
        return [dict(r) for r in c.fetchall()]


def update_vip_tier(tier_id: int, min_value: float, bonus_urls: int) -> bool:
    """Owner: update a single VIP tier's min_value and bonus_urls. Returns True on success."""
    if tier_id not in (0, 1, 2, 3, 4, 5):
        return False
    with _conn() as c:
        c.execute(
            "UPDATE vip_tiers_config SET min_value=%s, bonus_urls=%s WHERE tier_id=%s",
            (min_value, bonus_urls, tier_id)
        )
        return c.rowcount == 1


def get_vip_link_limits() -> dict:
    """Return bonus_urls for VIP tier 1 and 2 as vip1/vip2 link limits."""
    with _conn() as c:
        c.execute("SELECT tier_id, bonus_urls FROM vip_tiers_config WHERE tier_id IN (1,2)")
        rows = {r['tier_id']: r['bonus_urls'] for r in c.fetchall()}
    return {'vip1': rows.get(1, 50), 'vip2': rows.get(2, 150)}


# ── SHA-256 Access Key helpers ────────────────────────────────────────────────

def _new_sha256_token() -> str:
    """Generate a cryptographically random, unpredictable 64-character SHA-256 hex token."""
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


def generate_access_key_for_supporter(supporter_id: int) -> str | None:
    """Generate and store a fresh SHA-256 access_key for a supporter. Returns the key or None."""
    key = _new_sha256_token()
    with _conn() as c:
        c.execute(
            "UPDATE donation_ranking SET access_key=%s, device_signature=NULL, is_activated=FALSE, activated_at=NULL WHERE id=%s",
            (key, supporter_id)
        )
        if c.rowcount == 0:
            return None
    return key


def validate_access_key(access_key: str, browser_hash: str, browser_device_info: str | None = None) -> dict:
    """Validate a SHA-256 access key and bind device_signature (one-time activation).
    On success, self-destructs the key by overwriting it with a new random hash.
    Returns dict with 'ok' bool, 'error' string if failed, 'secret_token' on success."""
    with _conn() as c:
        c.execute(
            "SELECT id, is_activated, secret_token FROM donation_ranking WHERE access_key=%s",
            (access_key,)
        )
        row = c.fetchone()
        if not row:
            return {"ok": False, "error": "Chave de acesso inválida ou não encontrada."}
        if row['is_activated']:
            return {"ok": False, "error": "Esta chave de acesso já foi vinculada a outro navegador."}
        # Atomic: bind device_signature, activate, self-destruct key
        destroyed_key = _new_sha256_token()
        now_ts = datetime.utcnow().isoformat()
        c.execute(
            "UPDATE donation_ranking SET device_signature=%s, is_activated=TRUE, access_key=%s, activated_at=%s, browser_device_info=%s WHERE id=%s",
            (browser_hash, destroyed_key, now_ts, browser_device_info or None, row['id'])
        )
        return {"ok": True, "secret_token": row['secret_token']}


def audit_supporter(supporter_id: int) -> dict | None:
    """Return activation/device audit data for a supporter. Owner/admin only."""
    with _conn() as c:
        c.execute(
            "SELECT nickname, is_activated, device_signature, activated_at, browser_device_info FROM donation_ranking WHERE id=%s",
            (supporter_id,)
        )
        row = c.fetchone()
        if not row:
            return None
        return {
            "status": "success",
            "nickname": row['nickname'],
            "is_activated": bool(row['is_activated']),
            "device_signature": row['device_signature'] or "—",
            "activated_at": row['activated_at'] or "—",
            "browser_device_info": row['browser_device_info'] or "—",
        }


def regenerate_supporter_key(supporter_id: int) -> str | None:
    """Wipe device binding and issue a fresh SHA-256 access key. Owner only. Returns the new key."""
    new_key = _new_sha256_token()
    with _conn() as c:
        c.execute(
            "UPDATE donation_ranking SET access_key=%s, device_signature=NULL, is_activated=FALSE, activated_at=NULL WHERE id=%s",
            (new_key, supporter_id)
        )
        if c.rowcount == 0:
            return None
    return new_key


def check_returning_donor(token: str, ip: str, ua: str):
    """Return donor row dict if the donation_token cookie matches a ranking entry.

    Trust model: token-only, matching every other returning-donor signal in this
    app (the "Bem-vindo de volta" header and the VIP URL-bonus lookup both use
    get_vip_by_token — cookie alone, no IP/UA gate). This function used to also
    require an exact IP+UA match against the last stored values, but donors on
    mobile networks / rotating ISPs / proxies legitimately change IP between
    visits, which made this endpoint disagree with the header: it would say
    "not returning" right under a header that says "Bem-vindo de volta". ip/ua
    are still accepted as params (and still recorded on every donation via
    register_donation) for audit/forensics purposes, they just no longer gate
    the returning-donor decision here.
    """
    if not token:
        return None
    with _conn() as c:
        c.execute(
            "SELECT id, nickname, avatar_id, vip_level, total_donated, last_ip, last_ua, "
            "discriminator, font_id, text_effect, name_color "
            "FROM donation_ranking WHERE secret_token=%s",
            (token,)
        )
        row = c.fetchone()
        if not row:
            return None
        return dict(row)


class DuplicatePaymentError(Exception):
    """Raised when a payment has already been linked to a different ranking token."""


def _generate_discriminator(c, nickname: str) -> str:
    """Roll a unique 4-digit #Tag for a given nickname (checked inside current transaction).
    Loops up to 200 times before giving up (9 000 possible slots makes collision near-impossible)."""
    import random as _rand
    nick = nickname[:40]
    for _ in range(200):
        disc = str(_rand.randint(1000, 9999))
        c.execute(
            "SELECT 1 FROM donation_ranking WHERE nickname=%s AND discriminator=%s",
            (nick, disc)
        )
        if not c.fetchone():
            return disc
    return str(_rand.randint(1000, 9999))


def register_donation(nickname: str, avatar_id: str, amount: float, token: str,
                      payment_id: str = None, ip: str = None, ua: str = None,
                      font_id: str = None, text_effect: str = None,
                      name_color: str = None, currency: str = 'BRL') -> dict:
    """Insert or accumulate a donation; atomically claims the payment and upserts ranking.

    Raises DuplicatePaymentError if payment_id is already linked to a different token,
    preventing double-crediting of the same payment.
    ip / ua are stored for returning-donor identity verification.
    font_id is deliberately validated against a small server-side allowlist;
    the browser is never allowed to write arbitrary CSS into the ranking.
    """
    import datetime
    ts = datetime.datetime.utcnow().isoformat()
    allowed_fonts = {'default', 'inter', 'orbitron', 'rajdhani', 'space-grotesk',
                     'press-start', 'bangers', 'roboto-mono', 'montserrat',
                     'gothic', 'script', 'pixel'}
    allowed_effects = {'solid', 'gradient', 'neon', 'pop', 'gummy', 'prism'}
    font_id = font_id if font_id in allowed_fonts else None
    text_effect = text_effect if text_effect in allowed_effects else None
    if name_color is not None and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(name_color or '')):
        name_color = None
    if name_color is not None:
        name_color = str(name_color).lower()
    currency = str(currency or 'BRL').upper()
    if currency not in {'BRL', 'USD'}:
        currency = 'BRL'
    seen_at = time.time()
    with _lock, _conn() as c:
        # ── Step 1: Idempotent atomic payment claim ────────────────────────
        #   Only proceed with a ranking write when this payment is unclaimed.
        #   If already claimed by THIS token → idempotent re-submission (OK).
        #   If already claimed by a DIFFERENT token → reject (double-credit).
        first_claim = False
        if payment_id:
            # Try to claim payment that is currently unclaimed
            c.execute(
                "UPDATE pix_payments SET linked_ranking_token=%s "
                "WHERE payment_id=%s AND linked_ranking_token IS NULL",
                (token, payment_id)
            )
            if c.rowcount == 1:
                first_claim = True
            else:
                # Was not NULL — check who owns it
                c.execute(
                    "SELECT linked_ranking_token FROM pix_payments WHERE payment_id=%s",
                    (payment_id,)
                )
                pix_row = c.fetchone()
                if pix_row and pix_row['linked_ranking_token'] != token:
                    raise DuplicatePaymentError(
                        f"Payment {payment_id} already claimed by a different token"
                    )
                # Same token → idempotent re-submission (first_claim stays False,
                # amount is NOT added again below)

        # ── Step 2: Upsert ranking — only accumulate amount on first claim ──
        c.execute(
            "SELECT id, total_donated, discriminator, vip_level, font_id, text_effect, name_color "
            "FROM donation_ranking WHERE secret_token = %s", (token,)
        )
        existing = c.fetchone()
        if existing:
            font_id = font_id or existing.get('font_id') or 'default'
            text_effect = text_effect or existing.get('text_effect') or 'solid'
            name_color = name_color or existing.get('name_color') or '#f3f4f6'
            # Only add amount when this is genuinely the first claim of this payment
            added = amount if (first_claim or payment_id is None) else 0
            new_total = float(existing['total_donated']) + added
            vip = _compute_vip_level_with_conn(c, new_total)
            # Keep existing discriminator; generate one if the row was created before this feature
            disc = existing.get('discriminator') or _generate_discriminator(c, nickname)
            c.execute(
                "UPDATE donation_ranking SET total_donated=%s, vip_level=%s, avatar_id=%s, nickname=%s, "
                "currency=%s, last_ip=%s, last_ua=%s, discriminator=%s, font_id=%s, text_effect=%s, name_color=%s, last_seen_at=%s "
                "WHERE secret_token=%s",
                (new_total, vip, avatar_id, nickname[:40], currency, ip, ua, disc, font_id,
                 text_effect, name_color, seen_at, token)
            )
            # is_new=False signals a repeat donation from an already-known supporter
            # (secret_token already had a ranking row) — used by app.py to fire the
            # "apoiador de elite" repeat-donation notification/webhook.
            return {'id': existing['id'], 'total_donated': new_total, 'vip_level': vip,
                    'nickname': nickname[:40], 'discriminator': disc, 'font_id': font_id,
                    'text_effect': text_effect, 'name_color': name_color,
                    'is_new': False, 'previous_vip_level': int(existing['vip_level'])}
        else:
            font_id = font_id or 'default'
            text_effect = text_effect or 'solid'
            name_color = name_color or '#f3f4f6'
            credited = amount if (first_claim or payment_id is None) else 0
            vip = _compute_vip_level_with_conn(c, credited)
            disc = _generate_discriminator(c, nickname)
            c.execute(
                """INSERT INTO donation_ranking(
                   nickname, avatar_id, total_donated, currency, vip_level, secret_token, ts,
                   last_ip, last_ua, discriminator, font_id, text_effect, name_color, last_seen_at)
                   VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (nickname[:40], avatar_id, credited, currency, vip, token, ts, ip, ua, disc,
                  font_id, text_effect, name_color, seen_at)
            )
            row = c.fetchone()
            return {'id': row['id'], 'total_donated': credited, 'vip_level': vip,
                    'nickname': nickname[:40], 'discriminator': disc, 'font_id': font_id,
                    'text_effect': text_effect, 'name_color': name_color,
                    'is_new': True, 'previous_vip_level': None}


def get_ranking(limit: int = 50) -> list:
    """Return top donors by total, preserving first-support order on ties."""
    with _conn() as c:
        c.execute(
            "SELECT id, nickname, avatar_id, total_donated, vip_level, ts, discriminator, "
            "currency, font_id, text_effect, name_color, last_seen_at "
            "FROM donation_ranking ORDER BY total_donated DESC, id ASC LIMIT %s",
            (limit,)
        )
        rows = []
        allowed_fonts = {'default', 'inter', 'orbitron', 'rajdhani', 'space-grotesk',
                         'press-start', 'bangers', 'roboto-mono', 'montserrat',
                         'gothic', 'script', 'pixel'}
        allowed_effects = {'solid', 'gradient', 'neon', 'pop', 'gummy', 'prism'}
        for row in c.fetchall():
            item = dict(row)
            item['currency'] = str(item.get('currency') or 'BRL').upper()
            if item['currency'] not in {'BRL', 'USD'}:
                item['currency'] = 'BRL'
            if item.get('font_id') not in allowed_fonts:
                item['font_id'] = 'default'
            if item.get('text_effect') not in allowed_effects:
                item['text_effect'] = 'solid'
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(item.get('name_color') or '')):
                item['name_color'] = '#f3f4f6'
            rows.append(item)
        return rows


def get_vip_by_token(token: str):
    """Return vip_level, total_donated, and nickname for a secret_token, or None."""
    if not token:
        return None
    with _conn() as c:
        c.execute(
            "SELECT vip_level, total_donated, nickname, discriminator, avatar_id, font_id, "
            "text_effect, name_color "
            "FROM donation_ranking WHERE secret_token = %s",
            (token,)
        )
        row = c.fetchone()
        return dict(row) if row else None


def update_donor_style(token: str, font_id: str = 'default',
                       text_effect: str = 'solid',
                       name_color: str = '#f3f4f6') -> bool:
    """Persist only allowlisted public nickname styling for a donor token."""
    allowed_fonts = {'default', 'inter', 'orbitron', 'rajdhani', 'space-grotesk',
                     'press-start', 'bangers', 'roboto-mono', 'montserrat',
                     'gothic', 'script', 'pixel'}
    allowed_effects = {'solid', 'gradient', 'neon', 'pop', 'gummy', 'prism'}
    if font_id not in allowed_fonts:
        font_id = 'default'
    if text_effect not in allowed_effects:
        text_effect = 'solid'
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(name_color or '')):
        name_color = '#f3f4f6'
    with _lock, _conn() as c:
        c.execute(
            "UPDATE donation_ranking SET font_id=%s, text_effect=%s, name_color=%s "
            "WHERE secret_token=%s",
            (font_id, text_effect, str(name_color).lower(), token)
        )
        return c.rowcount == 1


def touch_supporter_presence(token: str) -> bool:
    """Mark a supporter as recently active without exposing their token."""
    if not token:
        return False
    with _conn() as c:
        c.execute(
            "UPDATE donation_ranking SET last_seen_at=%s WHERE secret_token=%s",
            (time.time(), token)
        )
        return c.rowcount > 0


def delete_ranking_entry(user_id: int) -> bool:
    """Delete a ranking entry by id. Returns True if deleted."""
    with _conn() as c:
        c.execute("DELETE FROM donation_ranking WHERE id = %s", (user_id,))
        return c.rowcount > 0


def _migrate_columns(c):
    """Add missing columns to existing tables using information_schema."""
    def _cols(table):
        c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=%s AND table_schema='public'",
            (table,)
        )
        return {row['column_name'] for row in c.fetchall()}

    existing = _cols('admins')
    if 'status' not in existing:
        c.execute("ALTER TABLE admins ADD COLUMN status TEXT NOT NULL DEFAULT 'offline'")
    if 'crowned' not in existing:
        c.execute("ALTER TABLE admins ADD COLUMN crowned INTEGER NOT NULL DEFAULT 0")
    added_dev_mode = 'access_dev_mode' not in existing
    added_gateways = 'manage_gateways' not in existing
    if added_dev_mode:
        c.execute("ALTER TABLE admins ADD COLUMN access_dev_mode BOOLEAN NOT NULL DEFAULT FALSE")
    if added_gateways:
        c.execute("ALTER TABLE admins ADD COLUMN manage_gateways BOOLEAN NOT NULL DEFAULT FALSE")
    if added_dev_mode:
        c.execute(
            "UPDATE admins SET access_dev_mode = (role IN ('owner','admin'))"
        )
    if added_gateways:
        # The former global gateway switch is intentionally not migrated into
        # every admin account. New access is granted explicitly per user.
        c.execute("UPDATE admins SET manage_gateways = FALSE")
    c.execute(
        "UPDATE admins SET access_dev_mode=TRUE, manage_gateways=TRUE WHERE role='owner'"
    )

    existing_log = _cols('activity_log')
    if 'hidden_by' not in existing_log:
        c.execute("ALTER TABLE activity_log ADD COLUMN hidden_by INTEGER DEFAULT NULL")
    if 'hidden_at' not in existing_log:
        c.execute("ALTER TABLE activity_log ADD COLUMN hidden_at TEXT DEFAULT NULL")

    existing_notif = _cols('notifications')
    if 'min_role' not in existing_notif:
        c.execute("ALTER TABLE notifications ADD COLUMN min_role TEXT NOT NULL DEFAULT 'admin'")
    if 'cleared_by' not in existing_notif:
        c.execute("ALTER TABLE notifications ADD COLUMN cleared_by TEXT NOT NULL DEFAULT '[]'")

    existing_pix = _cols('pix_payments')
    if 'currency' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN currency TEXT NOT NULL DEFAULT 'BRL'")
    if 'expires_at' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN expires_at TEXT")
    if 'linked_ranking_token' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN linked_ranking_token TEXT DEFAULT NULL")
    # Nickname/avatar/token capturados ANTES do redirect em fluxos de cartão
    # (Stripe Checkout, PayPal) — usados por fulfill_pix_payment() para
    # concluir o cadastro no ranking automaticamente quando o gateway
    # confirma o pagamento via webhook, sem depender do navegador voltar
    # à aba original.
    if 'nickname' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN nickname TEXT DEFAULT NULL")
    if 'avatar_id' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN avatar_id TEXT DEFAULT NULL")
    if 'donor_token' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN donor_token TEXT DEFAULT NULL")
    if 'font_id' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN font_id TEXT DEFAULT 'default'")
    if 'text_effect' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN text_effect TEXT NOT NULL DEFAULT 'solid'")
    if 'name_color' not in existing_pix:
        c.execute("ALTER TABLE pix_payments ADD COLUMN name_color TEXT NOT NULL DEFAULT '#f3f4f6'")

    existing_ranking = _cols('donation_ranking')
    if 'currency' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN currency TEXT NOT NULL DEFAULT 'BRL'")
    if 'font_id' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN font_id TEXT NOT NULL DEFAULT 'default'")
    if 'text_effect' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN text_effect TEXT NOT NULL DEFAULT 'solid'")
    if 'name_color' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN name_color TEXT NOT NULL DEFAULT '#f3f4f6'")
    if 'last_seen_at' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN last_seen_at DOUBLE PRECISION")
    if 'last_ip' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN last_ip TEXT DEFAULT NULL")
    if 'last_ua' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN last_ua TEXT DEFAULT NULL")
    if 'discriminator' not in existing_ranking:
        pass  # handled below
    if 'access_key' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN access_key VARCHAR DEFAULT NULL")
    if 'device_signature' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN device_signature VARCHAR DEFAULT NULL")
    if 'is_activated' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN is_activated BOOLEAN DEFAULT FALSE")
    if 'activated_at' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN activated_at TEXT DEFAULT NULL")
    if 'browser_device_info' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN browser_device_info TEXT DEFAULT NULL")
    if 'discriminator' not in existing_ranking:
        c.execute("ALTER TABLE donation_ranking ADD COLUMN discriminator TEXT DEFAULT NULL")
        # Backfill existing rows with a random 4-digit tag
        c.execute(
            "UPDATE donation_ranking "
            "SET discriminator = LPAD((FLOOR(RANDOM()*9000)+1000)::INTEGER::TEXT, 4, '0') "
            "WHERE discriminator IS NULL"
        )

    existing_lockouts = _cols('ip_lockouts')
    if 'attempted_username' not in existing_lockouts:
        c.execute("ALTER TABLE ip_lockouts ADD COLUMN attempted_username TEXT")
    if 'hardware_info' not in existing_lockouts:
        c.execute("ALTER TABLE ip_lockouts ADD COLUMN hardware_info TEXT")

    existing_unlock = _cols('lockout_unlock_requests')
    if existing_unlock and 'attempted_username' not in existing_unlock:
        c.execute("ALTER TABLE lockout_unlock_requests ADD COLUMN attempted_username TEXT")
    if existing_unlock and 'hardware_info' not in existing_unlock:
        c.execute("ALTER TABLE lockout_unlock_requests ADD COLUMN hardware_info TEXT")

    c.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='suspicious_requests'"
    )
    if not c.fetchone():
        c.execute("""
            CREATE TABLE suspicious_requests (
                id          SERIAL PRIMARY KEY,
                ip          TEXT NOT NULL,
                attack_type TEXT NOT NULL,
                url_path    TEXT NOT NULL,
                method      TEXT NOT NULL DEFAULT 'GET',
                ts          TEXT NOT NULL,
                count       INTEGER NOT NULL DEFAULT 1
            )
        """)

    # brute_force_incidents: create if absent (older deployments won't have it)
    c.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='brute_force_incidents'"
    )
    if not c.fetchone():
        c.execute("""
            CREATE TABLE brute_force_incidents (
                id                SERIAL PRIMARY KEY,
                ip                TEXT NOT NULL,
                username_tried    TEXT,
                failures          INTEGER NOT NULL DEFAULT 0,
                ts                TEXT NOT NULL,
                ua                TEXT,
                geo_city          TEXT,
                geo_region        TEXT,
                geo_country       TEXT,
                geo_lat           TEXT,
                geo_lon           TEXT,
                geo_isp           TEXT,
                geo_asn           TEXT,
                geo_is_proxy      BOOLEAN NOT NULL DEFAULT FALSE,
                geo_is_hosting    BOOLEAN NOT NULL DEFAULT FALSE,
                hardware_json     TEXT,
                canvas_fp         TEXT,
                webrtc_ip         TEXT,
                browser_name      TEXT,
                operating_system  TEXT,
                hardware_ram      TEXT,
                hardware_gpu      TEXT,
                hardware_cores    TEXT,
                screen_resolution TEXT
            )
        """)

    # Deep forensic enrichment columns (browser/OS/hardware) on existing deployments
    existing_bf = _cols('brute_force_incidents')
    if existing_bf:
        for col in ('browser_name', 'operating_system', 'hardware_ram',
                    'hardware_gpu', 'hardware_cores', 'screen_resolution'):
            if col not in existing_bf:
                c.execute(f"ALTER TABLE brute_force_incidents ADD COLUMN {col} TEXT")

    existing_sus = _cols('suspicious_requests')
    if existing_sus:
        for col in ('ua', 'browser_name', 'operating_system'):
            if col not in existing_sus:
                c.execute(f"ALTER TABLE suspicious_requests ADD COLUMN {col} TEXT")

    # avatar_id: widen from INTEGER to TEXT to support gender-prefixed ids
    # such as "m_1".."m_6" (male) and "f_1".."f_6" (female).
    c.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='donation_ranking' AND column_name='avatar_id'"
    )
    avatar_col = c.fetchone()
    if avatar_col and avatar_col['data_type'] != 'text':
        c.execute("ALTER TABLE donation_ranking ALTER COLUMN avatar_id DROP DEFAULT")
        c.execute("ALTER TABLE donation_ranking ALTER COLUMN avatar_id TYPE TEXT USING 'm_' || avatar_id::text")
        c.execute("ALTER TABLE donation_ranking ALTER COLUMN avatar_id SET DEFAULT 'm_1'")


def _seed_owner(c):
    now = _now()
    seeds = [
        ('owner',     os.environ.get('ADMIN_OWNER_PASSWORD',    'Owner@2024!'),  'owner',     'av5', 'Owner',         'invisible'),
        ('admin',     os.environ.get('ADMIN_ADMIN_PASSWORD',    ''),             'admin',     'av3', 'Administrador', 'offline'),
        ('moderador', os.environ.get('ADMIN_MOD_PASSWORD',      ''),             'moderator', 'av2', 'Moderador',     'offline'),
        ('ajudante',  os.environ.get('ADMIN_AJUDANTE_PASSWORD', ''),             'helper',    'av4', 'Ajudante',      'offline'),
    ]
    for username, pw, role, avatar, display_name, status in seeds:
        if not pw:
            continue
        c.execute(
            "INSERT INTO admins (username, password, role, avatar, display_name, created_at, status, "
            "access_dev_mode, manage_gateways) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING",
            (username, generate_password_hash(pw), role, avatar, display_name, now, status,
             role == 'owner', role == 'owner')
        )


# ── Brute-force protection ────────────────────────────────────────────────

def is_locked(ip: str):
    with _conn() as c:
        c.execute(
            "SELECT failures, locked_until FROM ip_lockouts WHERE ip=%s", (ip,)
        )
        row = c.fetchone()
    if not row or not row['locked_until']:
        return False, 0
    lu = datetime.fromisoformat(row['locked_until'])
    if datetime.now() < lu:
        mins = max(1, int((lu - datetime.now()).total_seconds() / 60))
        return True, mins
    return False, 0


def get_lockout_info(ip: str):
    """Returns dict with failures, locked_until, attempted_username, hardware_info or None."""
    with _conn() as c:
        c.execute(
            "SELECT failures, locked_until, attempted_username, hardware_info FROM ip_lockouts WHERE ip=%s", (ip,)
        )
        row = c.fetchone()
    return dict(row) if row else None


def record_hardware(ip: str, hardware_info: str):
    with _lock, _conn() as c:
        c.execute(
            "UPDATE ip_lockouts SET hardware_info=%s WHERE ip=%s",
            (hardware_info, ip)
        )


def record_failure(ip: str, attempted_username: str = None):
    with _lock, _conn() as c:
        c.execute("SELECT failures FROM ip_lockouts WHERE ip=%s", (ip,))
        row   = c.fetchone()
        fails = (row['failures'] if row else 0) + 1
        lu    = _now(datetime.now() + timedelta(minutes=LOCKOUT_MIN)) if fails >= LOCKOUT_AFTER else None
        c.execute(
            "INSERT INTO ip_lockouts(ip, failures, locked_until, attempted_username) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT(ip) DO UPDATE SET failures=excluded.failures, locked_until=excluded.locked_until, "
            "attempted_username=excluded.attempted_username",
            (ip, fails, lu, attempted_username)
        )
    return fails


def clear_failures(ip: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM ip_lockouts WHERE ip=%s", (ip,))


# ── Suspicious request detection ──────────────────────────────────────────

def record_suspicious_request(ip: str, attack_type: str, url_path: str, method: str = 'GET',
                              ua: str = '', browser_name: str = '', operating_system: str = ''):
    today = datetime.now().strftime('%Y-%m-%d')
    ua = (ua or '')[:300]
    browser_name = (browser_name or '')[:80]
    operating_system = (operating_system or '')[:80]
    with _lock, _conn() as c:
        c.execute(
            "SELECT id, count FROM suspicious_requests "
            "WHERE ip=%s AND attack_type=%s AND url_path=%s AND ts LIKE %s",
            (ip, attack_type, url_path[:500], f'{today}%')
        )
        row = c.fetchone()
        if row:
            c.execute(
                "UPDATE suspicious_requests "
                "SET count=%s, ts=%s, ua=%s, browser_name=%s, operating_system=%s WHERE id=%s",
                (row['count'] + 1, _now(), ua, browser_name, operating_system, row['id'])
            )
        else:
            c.execute(
                "INSERT INTO suspicious_requests"
                "(ip, attack_type, url_path, method, ts, count, ua, browser_name, operating_system) "
                "VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s)",
                (ip, attack_type, url_path[:500], method, _now(), ua, browser_name, operating_system)
            )


# ── WAF first-strike warning functions (two-strikes system) ──────────────

def record_waf_warning(ip: str, reason: str, url_path: str,
                       method: str = 'GET', ua: str = '') -> int:
    """Record or increment a first-strike WAF warning for an IP.
    Returns the new cumulative count."""
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO waf_warnings (ip, count, reason, url_path, method, ua, ts)
            VALUES (%s, 1, %s, %s, %s, %s, %s)
            ON CONFLICT (ip) DO UPDATE
              SET count    = waf_warnings.count + 1,
                  reason   = EXCLUDED.reason,
                  url_path = EXCLUDED.url_path,
                  method   = EXCLUDED.method,
                  ua       = EXCLUDED.ua,
                  ts       = EXCLUDED.ts
            RETURNING count
        """, (ip, reason[:255], url_path[:500], method, (ua or '')[:300], _now()))
        row = c.fetchone()
        return row['count'] if row else 1


def get_waf_warning(ip: str) -> 'dict | None':
    """Return the warning record for an IP, or None if clean."""
    with _conn() as c:
        c.execute("SELECT * FROM waf_warnings WHERE ip = %s", (ip,))
        return c.fetchone()


def clear_waf_warning(ip: str) -> None:
    """Remove the warning record once an IP is permanently banned."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM waf_warnings WHERE ip = %s", (ip,))


def get_waf_status(ip: str) -> dict:
    """Return a dict with waf_status ('banned'|'warning'|'clean') and waf_count."""
    ban  = is_ip_banned_waf(ip)
    if ban:
        return {'waf_status': 'banned', 'waf_count': 2}
    warn = get_waf_warning(ip)
    if warn:
        return {'waf_status': 'warning', 'waf_count': int(warn['count'])}
    return {'waf_status': 'clean', 'waf_count': 0}


# ── WAF auto-ban functions ────────────────────────────────────────────────

def ban_ip_waf(ip: str, reason: str, url_path: str,
               method: str = 'GET', ua: str = '') -> None:
    """Permanently ban an IP via the WAF. Idempotent: re-banning updates meta."""
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO waf_bans (ip, reason, url_path, method, ua, ts)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (ip) DO UPDATE
              SET reason   = EXCLUDED.reason,
                  url_path = EXCLUDED.url_path,
                  method   = EXCLUDED.method,
                  ua       = EXCLUDED.ua,
                  ts       = EXCLUDED.ts,
                  unban_ts = NULL
        """, (ip, reason[:255], url_path[:500], method, (ua or '')[:300], _now()))


def is_ip_banned_waf(ip: str) -> dict | None:
    """Return ban record dict if IP is banned (and unban_ts is null or future),
    else None."""
    with _conn() as c:
        c.execute(
            "SELECT * FROM waf_bans WHERE ip=%s AND (unban_ts IS NULL OR unban_ts > %s)",
            (ip, _now())
        )
        return c.fetchone()


def get_waf_bans(limit: int = 200) -> list:
    """Return the most recent WAF bans."""
    with _conn() as c:
        c.execute(
            "SELECT * FROM waf_bans ORDER BY ts DESC LIMIT %s", (limit,)
        )
        return c.fetchall() or []


# ── WAF Whitelist (waf_lista_branca) CRUD ─────────────────────────────────

def get_whitelist() -> list:
    """Return all whitelist entries ordered by creation date."""
    with _conn() as c:
        c.execute("SELECT * FROM waf_lista_branca ORDER BY criado_em DESC")
        return [dict(r) for r in c.fetchall()]


def add_to_whitelist(nome_dev: str, ip: str = '', dispositivo_id: str = '',
                     modo_teste_ativo: bool = False) -> None:
    """Insert a new developer/pentester into the whitelist.

    Uses two code-paths:
    - If dispositivo_id is provided → ON CONFLICT against the partial unique index
      (only works when the value is non-NULL and non-empty, matching the index predicate).
    - If dispositivo_id is absent → plain INSERT (no conflict key available for NULLs).
    """
    dev_id = (dispositivo_id or '').strip() or None
    with _lock, _conn() as c:
        if dev_id:
            c.execute("""
                INSERT INTO waf_lista_branca (nome_dev, ip, dispositivo_id, modo_teste_ativo, criado_em)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (dispositivo_id)
                WHERE dispositivo_id IS NOT NULL AND dispositivo_id <> ''
                DO UPDATE
                  SET nome_dev        = EXCLUDED.nome_dev,
                      ip              = EXCLUDED.ip,
                      modo_teste_ativo = EXCLUDED.modo_teste_ativo,
                      ultimo_login    = %s
            """, (nome_dev, ip or None, dev_id, modo_teste_ativo, _now(), _now()))
        else:
            c.execute("""
                INSERT INTO waf_lista_branca (nome_dev, ip, dispositivo_id, modo_teste_ativo, criado_em)
                VALUES (%s, %s, %s, %s, %s)
            """, (nome_dev, ip or None, None, modo_teste_ativo, _now()))


def remove_from_whitelist(entry_id: int) -> None:
    """Remove a whitelist entry by its primary key."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM waf_lista_branca WHERE id = %s", (entry_id,))


def is_ip_whitelisted(ip: str, dispositivo_id: str = '') -> bool:
    """Return True if IP or device fingerprint is in the whitelist with modo_teste_ativo."""
    with _conn() as c:
        if ip:
            c.execute(
                "SELECT 1 FROM waf_lista_branca WHERE ip = %s AND modo_teste_ativo = TRUE",
                (ip,)
            )
            if c.fetchone():
                return True
        if dispositivo_id:
            c.execute(
                "SELECT 1 FROM waf_lista_branca WHERE dispositivo_id = %s AND modo_teste_ativo = TRUE",
                (dispositivo_id,)
            )
            if c.fetchone():
                return True
    return False


def auto_register_staff_whitelist(nome_dev: str, ip: str, dispositivo_id: str) -> bool:
    """Auto-register (or refresh) a staff member's derived device fingerprint in the
    WAF whitelist when they log in successfully.

    - If an entry with the same ``dispositivo_id`` already exists it is refreshed
      (IP + ultimo_login updated, modo_teste_ativo kept TRUE).
    - Otherwise a new row is inserted.

    Returns True if a new entry was created, False if an existing one was refreshed.
    """
    if not dispositivo_id:
        return False
    now = _now()
    with _lock, _conn() as c:
        c.execute(
            "SELECT id FROM waf_lista_branca WHERE dispositivo_id = %s",
            (dispositivo_id,)
        )
        existing = c.fetchone()
        if existing:
            c.execute("""
                UPDATE waf_lista_branca
                SET ip = %s, ultimo_login = %s, modo_teste_ativo = TRUE
                WHERE dispositivo_id = %s
            """, (ip or None, now, dispositivo_id))
            return False
        else:
            c.execute("""
                INSERT INTO waf_lista_branca
                    (nome_dev, ip, dispositivo_id, modo_teste_ativo, ultimo_login, criado_em)
                VALUES (%s, %s, %s, TRUE, %s, %s)
            """, (nome_dev, ip or None, dispositivo_id, now, now))
            return True


def sync_waf_bans_from_history() -> dict:
    """Scan suspicious_requests and waf_warnings to auto-import malicious IPs
    into waf_bans with intelligently detected reasons.
    Returns {'imported': N, 'skipped': N, 'no_history': [ips]}."""
    _PATH_TRAVERSAL_RE = re.compile(r'\.\.|%2e%2e|%252e|etc/passwd|etc%2fpasswd', re.I)
    _ENV_RE            = re.compile(r'\.env|config\.php|wp-config|secrets?\b', re.I)
    _SCAN_RE           = re.compile(r'phpinfo|autodiscover|\.git|\.svn|sqlmap', re.I)
    imported = 0
    skipped  = 0
    no_hist  = []
    with _conn() as c:
        c.execute("""
            SELECT ip, url_path
            FROM suspicious_requests
            WHERE ip NOT IN (SELECT ip FROM waf_bans)
            GROUP BY ip, url_path
            ORDER BY ip
        """)
        rows = c.fetchall()

    ip_url: dict[str, str] = {}
    for r in rows:
        ip_url.setdefault(r['ip'], r['url_path'])

    now = _now()
    for ip, sample_url in ip_url.items():
        if _PATH_TRAVERSAL_RE.search(sample_url):
            reason = 'Exploração de vulnerabilidade via Path Traversal'
        elif _ENV_RE.search(sample_url):
            reason = 'Tentativa de vazamento de credenciais (.env / arquivos de configuração)'
        elif _SCAN_RE.search(sample_url):
            reason = 'Scanner Automatizado de Vulnerabilidades (Bot/Robô)'
        else:
            reason = 'Atividade suspeita detectada automaticamente'
            no_hist.append(ip)
        try:
            with _lock, _conn() as c:
                c.execute("""
                    INSERT INTO waf_bans (ip, reason, url_path, method, ua, ts)
                    VALUES (%s, %s, %s, 'GET', '[Sync automático de dossiês]', %s)
                    ON CONFLICT (ip) DO NOTHING
                """, (ip, reason, sample_url[:500], now))
                if c.rowcount:
                    imported += 1
                else:
                    skipped += 1
        except Exception:
            skipped += 1
    return {'imported': imported, 'skipped': skipped, 'no_history': no_hist}


def unban_ip_waf(ip: str) -> None:
    """Lift a WAF ban immediately."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM waf_bans WHERE ip=%s", (ip,))


# ── Brute-force incident forensics ───────────────────────────────────────

def record_brute_force_incident(ip: str, username: str, failures: int,
                                geo_data: dict, ua: str,
                                hardware_json: str = '', canvas_fp: str = '',
                                webrtc_ip: str = '', browser_name: str = '',
                                operating_system: str = '') -> int:
    """Record a confirmed brute-force lockout with full OSINT + forensic data.
    Returns the new incident id."""
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO brute_force_incidents
              (ip, username_tried, failures, ts, ua,
               geo_city, geo_region, geo_country, geo_lat, geo_lon,
               geo_isp, geo_asn, geo_is_proxy, geo_is_hosting,
               hardware_json, canvas_fp, webrtc_ip,
               browser_name, operating_system)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s)
        """, (
            ip,
            (username or '')[:64],
            failures,
            _now(),
            (ua or '')[:300],
            geo_data.get('city', ''),
            geo_data.get('region', ''),
            geo_data.get('country', ''),
            geo_data.get('lat', ''),
            geo_data.get('lon', ''),
            geo_data.get('isp', '') or geo_data.get('org', ''),
            geo_data.get('asn', ''),
            bool(geo_data.get('is_proxy', False)),
            bool(geo_data.get('is_hosting', False)),
            (hardware_json or '')[:2000],
            (canvas_fp or '')[:64],
            (webrtc_ip or '')[:64],
            (browser_name or '')[:80],
            (operating_system or '')[:80],
        ))
        c.execute("SELECT lastval()")
        row = c.fetchone()
    return row[0] if row else 0


def get_linked_ips_for_ip(ip: str) -> list:
    """Return other IPs that share the same hardware canvas fingerprint as the given IP.
    Cross-references brute_force_incidents for VPN evasion detection in the
    suspicious-requests accordion view."""
    with _conn() as c:
        c.execute(
            "SELECT DISTINCT canvas_fp FROM brute_force_incidents "
            "WHERE ip = %s AND canvas_fp IS NOT NULL AND canvas_fp != ''",
            (ip,)
        )
        fps = [row['canvas_fp'] for row in c.fetchall()]
    if not fps:
        return []
    linked: list = []
    seen: set = set()
    for fp in fps:
        for other in get_linked_ips_by_fingerprint(fp, exclude_ip=ip):
            if other['ip'] not in seen:
                seen.add(other['ip'])
                linked.append(other['ip'])
    return linked


def get_linked_ips_by_fingerprint(canvas_fp: str, exclude_ip: str = '') -> list:
    """Return distinct IPs that share the same canvas fingerprint (same physical device,
    different external IP — indicates VPN rotation or NAT change)."""
    if not canvas_fp:
        return []
    with _conn() as c:
        c.execute(
            "SELECT ip, geo_country, geo_city, geo_isp, geo_is_proxy, "
            "operating_system, browser_name, MIN(ts) AS first_seen, MAX(ts) AS last_seen, "
            "COUNT(*) AS incident_count "
            "FROM brute_force_incidents "
            "WHERE canvas_fp = %s AND ip != %s "
            "GROUP BY ip, geo_country, geo_city, geo_isp, geo_is_proxy, operating_system, browser_name "
            "ORDER BY last_seen DESC",
            (canvas_fp, exclude_ip or ''),
        )
        return [dict(r) for r in c.fetchall()]


def update_incident_fingerprint(ip: str, canvas_fp: str,
                                webrtc_ip: str, hardware_json: str,
                                hardware_ram: str = '', hardware_gpu: str = '',
                                hardware_cores: str = '', screen_resolution: str = ''):
    """Update the most recent brute-force incident for this IP with
    forensic fingerprint data sent from the locked browser.
    Auto-links the incident to any existing dossier that shares the same hardware fingerprint
    (same device, different external IP — VPN rotation / IP change detected)."""
    with _lock, _conn() as c:
        c.execute("""
            UPDATE brute_force_incidents
               SET canvas_fp         = %s,
                   webrtc_ip         = %s,
                   hardware_json     = %s,
                   hardware_ram      = %s,
                   hardware_gpu      = %s,
                   hardware_cores    = %s,
                   screen_resolution = %s
             WHERE id = (
                 SELECT id FROM brute_force_incidents
                  WHERE ip = %s
                  ORDER BY ts DESC
                  LIMIT 1
             )
        """, (
            (canvas_fp or '')[:64],
            (webrtc_ip or '')[:64],
            (hardware_json or '')[:2000],
            (hardware_ram or '')[:64],
            (hardware_gpu or '')[:150],
            (hardware_cores or '')[:32],
            (screen_resolution or '')[:32],
            ip,
        ))

    # ── Auto-report: same hardware fingerprint detected on a different IP ──
    if canvas_fp:
        linked = get_linked_ips_by_fingerprint(canvas_fp, exclude_ip=ip)
        if linked:
            linked_ip_list = ', '.join(r['ip'] for r in linked)
            detail = (
                f"HARDWARE VINCULADO AUTOMATICAMENTE — IP {ip} detectado com o mesmo "
                f"fingerprint de hardware (canvas_fp={canvas_fp[:16]}…) já registrado nos "
                f"IPs: {linked_ip_list}. Possível troca de IP/VPN pelo mesmo dispositivo físico."
            )
            with _lock, _conn() as c:
                c.execute("""
                    INSERT INTO audit_log (admin_id, admin_name, role, action, detail, ts, ip)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (
                    0, 'SISTEMA', 'system', 'hardware_link_detected',
                    detail[:1000], _now(), ip,
                ))


def get_brute_force_incidents(limit: int = 200) -> list:
    with _conn() as c:
        c.execute(
            "SELECT * FROM brute_force_incidents ORDER BY ts DESC LIMIT %s",
            (limit,)
        )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def count_brute_force_today() -> int:
    """Rolling 24h lookback instead of a hardcoded calendar-day boundary —
    a fixed 'YYYY-MM-DD' match is wrong whenever the server clock's timezone
    (e.g. UTC on some hosts) doesn't match the admin's local day, silently
    hiding incidents that happened late in the local day but rolled into the
    next UTC date (or vice-versa)."""
    cutoff = _now(datetime.now() - timedelta(hours=24))
    with _conn() as c:
        c.execute(
            "SELECT COUNT(*) FROM brute_force_incidents WHERE ts >= %s",
            (cutoff,)
        )
        row = c.fetchone()
    return row[0] if row else 0


def get_suspicious_requests(limit: int = 200) -> list:
    with _conn() as c:
        c.execute(
            "SELECT * FROM suspicious_requests ORDER BY ts DESC LIMIT %s", (limit,)
        )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def count_suspicious_today() -> int:
    today = datetime.now().strftime('%Y-%m-%d')
    with _conn() as c:
        c.execute(
            "SELECT COUNT(*) AS n FROM suspicious_requests WHERE ts LIKE %s",
            (f'{today}%',)
        )
        row = c.fetchone()
    return row['n'] if row else 0


def clear_suspicious_requests():
    with _lock, _conn() as c:
        c.execute("DELETE FROM suspicious_requests")


def delete_suspicious_by_ip(ip: str) -> int:
    """Delete ALL suspicious_requests records for a specific IP.
    Returns the number of rows deleted."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM suspicious_requests WHERE ip = %s", (ip,))
        return c.rowcount


def get_all_events_by_ip(ip: str) -> dict:
    """Collect every logged event tied to a specific IP — used for dossier export.
    Also includes linked IPs (same hardware fingerprint, different external IP)."""
    with _conn() as c:
        c.execute(
            "SELECT id, ts, attack_type, method, url_path, count, "
            "ua, browser_name, operating_system "
            "FROM suspicious_requests WHERE ip=%s ORDER BY ts ASC",
            (ip,)
        )
        suspicious = [dict(r) for r in c.fetchall()]

        c.execute(
            "SELECT id, ts, username_tried, failures, ua, "
            "geo_country, geo_region, geo_city, geo_lat, geo_lon, "
            "geo_isp, geo_asn, geo_is_proxy, geo_is_hosting, "
            "canvas_fp, webrtc_ip, browser_name, operating_system, "
            "hardware_ram, hardware_gpu, hardware_cores, screen_resolution "
            "FROM brute_force_incidents WHERE ip=%s ORDER BY ts ASC",
            (ip,)
        )
        brute_force = [dict(r) for r in c.fetchall()]

        c.execute(
            "SELECT id, ts, admin_name, role, action, detail "
            "FROM audit_log WHERE ip=%s ORDER BY ts ASC",
            (ip,)
        )
        audit_entries = [dict(r) for r in c.fetchall()]

    # Resolve linked IPs via shared canvas fingerprint
    canvas_fps = list({r['canvas_fp'] for r in brute_force if r.get('canvas_fp')})
    linked_ips: list = []
    if canvas_fps:
        seen: set = set()
        for fp in canvas_fps:
            for row in get_linked_ips_by_fingerprint(fp, exclude_ip=ip):
                if row['ip'] not in seen:
                    seen.add(row['ip'])
                    row['canvas_fp'] = fp
                    linked_ips.append(row)

    return {
        'suspicious':   suspicious,
        'brute_force':  brute_force,
        'audit':        audit_entries,
        'linked_ips':   linked_ips,
    }


# ── Authentication ────────────────────────────────────────────────────────

def authenticate(username: str, password: str):
    with _conn() as c:
        c.execute(
            "SELECT * FROM admins WHERE username=%s AND active=1", (username,)
        )
        row = c.fetchone()
    if row and check_password_hash(row['password'], password):
        return dict(row)
    return None


def create_session(admin_id: int, ip: str) -> str:
    token   = secrets.token_hex(32)
    now     = _now()
    expires = _now(datetime.now() + timedelta(hours=SESSION_TTL_H))
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE admin_id=%s", (admin_id,))
        c.execute(
            "INSERT INTO sessions(token, admin_id, ip, created_at, last_seen, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (token, admin_id, ip, now, now, expires)
        )
    return token


def get_session(token: str):
    if not token:
        return None, None
    with _conn() as c:
        c.execute("SELECT * FROM sessions WHERE token=%s", (token,))
        sess = c.fetchone()
        if not sess:
            return None, None
        if datetime.now() > datetime.fromisoformat(sess['expires_at']):
            c.execute("DELETE FROM sessions WHERE token=%s", (token,))
            return None, None
        c.execute(
            "SELECT * FROM admins WHERE id=%s AND active=1", (sess['admin_id'],)
        )
        admin = c.fetchone()
        if not admin:
            return None, None
        c.execute("UPDATE sessions SET last_seen=%s WHERE token=%s", (_now(), token))
    return dict(admin), dict(sess)


def delete_session(token: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=%s", (token,))


def get_active_sessions(viewer_role: str = 'owner'):
    """Return active sessions visible to the given viewer role.
    Mirrors the same privacy rule as get_online_members(): an invisible
    Owner is fully omitted from the list (and therefore from the count) for
    ALL viewers, including the Owner's own view of the panel — 'invisível'
    means invisible, full stop, not just hidden from non-owners."""
    with _conn() as c:
        c.execute("""
            SELECT a.id, a.display_name, a.role, a.avatar, a.status, a.crowned,
                   s.token, s.ip, s.last_seen, s.created_at, s.token as session_id
            FROM sessions s JOIN admins a ON s.admin_id = a.id
            WHERE s.expires_at > %s
            ORDER BY s.last_seen DESC
        """, (_now(),))
        rows = c.fetchall()
    all_sessions = [dict(r) for r in rows]
    seen   = set()
    unique = []
    for s in all_sessions:
        # Privacy rule: an invisible owner is hidden from everyone, always.
        if s['role'] == 'owner' and s['status'] == 'invisible':
            continue
        key = (s['id'], s['ip'] or '')
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def terminate_session_by_token(token: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=%s", (token,))


def terminate_sessions_by_admin(admin_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE admin_id=%s", (admin_id,))


# ── Status ────────────────────────────────────────────────────────────────

def set_status(admin_id: int, status: str):
    if status not in STATUSES:
        raise ValueError("Status inválido")
    with _lock, _conn() as c:
        c.execute("UPDATE admins SET status=%s WHERE id=%s", (status, admin_id))


def get_online_members(viewer_role: str):
    """Lista membros 'online' que possuem sessão ativa de fato — evita
    sessões fantasmas (status desatualizado após expirar sem logout)."""
    with _conn() as c:
        c.execute(
            "SELECT DISTINCT a.id, a.username, a.display_name, a.role, a.avatar, "
            "a.status, a.crowned FROM admins a "
            "JOIN sessions s ON s.admin_id = a.id "
            "WHERE a.active=1 AND s.expires_at > %s",
            (_now(),)
        )
        rows = c.fetchall()
    result = []
    for r in rows:
        r = dict(r)
        # An invisible owner is hidden from every viewer, including the
        # Owner's own session — 'invisível' must mean invisible to all.
        if r['status'] == 'invisible' and r['role'] == 'owner':
            continue
        if r['status'] in ('online', 'busy', 'invisible'):
            result.append(r)
    return result


# ── Admins CRUD ───────────────────────────────────────────────────────────

def list_admins():
    with _conn() as c:
        c.execute("SELECT * FROM admins ORDER BY id")
        rows = c.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['permissions'] = {
            'accessDevMode': bool(item.get('access_dev_mode')),
            'manageGateways': bool(item.get('manage_gateways')),
        }
        result.append(item)
    return sorted(result, key=lambda r: (ROLE_LEVEL.get(r['role'], 99), r['username']))


def get_admin_by_id(admin_id: int):
    with _conn() as c:
        c.execute("SELECT * FROM admins WHERE id=%s", (admin_id,))
        row = c.fetchone()
    if not row:
        return None
    result = dict(row)
    result['permissions'] = {
        'accessDevMode': bool(result.get('access_dev_mode')),
        'manageGateways': bool(result.get('manage_gateways')),
    }
    return result


def create_admin(username: str, password: str, role: str,
                 display_name: str, avatar: str, created_by: str) -> int:
    if role not in ROLES:
        raise ValueError("Cargo inválido")
    if avatar not in AVATARS:
        avatar = 'av1'
    now = _now()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO admins(username, password, role, avatar, display_name, created_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (username.strip(), generate_password_hash(password),
             role, avatar, display_name.strip(), now, created_by)
        )
        return c.fetchone()['id']


def update_role(admin_id: int, new_role: str):
    if new_role not in ROLES:
        raise ValueError("Cargo inválido")
    with _lock, _conn() as c:
        c.execute("UPDATE admins SET role=%s WHERE id=%s", (new_role, admin_id))


def toggle_active(admin_id: int, active: bool):
    with _lock, _conn() as c:
        c.execute("UPDATE admins SET active=%s WHERE id=%s", (1 if active else 0, admin_id))
        if not active:
            c.execute("DELETE FROM sessions WHERE admin_id=%s", (admin_id,))


def update_avatar(admin_id: int, avatar: str):
    if avatar not in AVATARS:
        raise ValueError("Avatar inválido")
    with _lock, _conn() as c:
        c.execute("UPDATE admins SET avatar=%s WHERE id=%s", (avatar, admin_id))


def update_username(admin_id: int, new_username: str):
    new_username = new_username.strip()[:32]
    if not new_username:
        raise ValueError("Username não pode ser vazio")
    with _lock, _conn() as c:
        c.execute("UPDATE admins SET username=%s WHERE id=%s", (new_username, admin_id))


def update_password(admin_id: int, new_password: str):
    with _lock, _conn() as c:
        c.execute(
            "UPDATE admins SET password=%s WHERE id=%s",
            (generate_password_hash(new_password), admin_id)
        )


def delete_admin(admin_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE admin_id=%s", (admin_id,))
        c.execute("DELETE FROM admins WHERE id=%s", (admin_id,))


ADMIN_PERMISSION_COLUMNS = {
    'accessDevMode': 'access_dev_mode',
    'manageGateways': 'manage_gateways',
}


def get_admin_permissions(admin_id: int) -> dict:
    with _conn() as c:
        c.execute(
            "SELECT access_dev_mode, manage_gateways FROM admins WHERE id=%s",
            (admin_id,)
        )
        row = c.fetchone()
    if not row:
        return {'accessDevMode': False, 'manageGateways': False}
    return {
        'accessDevMode': bool(row['access_dev_mode']),
        'manageGateways': bool(row['manage_gateways']),
    }


def has_admin_permission(admin, permission: str) -> bool:
    """Check the per-user permission flags, never the static role alone."""
    if not admin:
        return False
    column = ADMIN_PERMISSION_COLUMNS.get(permission, permission)
    return bool(admin.get(column, False))


def ensure_owner_permissions(admin_id: int) -> None:
    """Owners retain both built-in controls even when their row predates flags."""
    with _lock, _conn() as c:
        c.execute(
            "UPDATE admins SET access_dev_mode=TRUE, manage_gateways=TRUE "
            "WHERE id=%s AND role='owner'",
            (admin_id,)
        )


def update_admin_permission(admin_id: int, permission: str, enabled: bool) -> dict | None:
    """Persist one allowlisted permission and return the resulting permission map."""
    column = ADMIN_PERMISSION_COLUMNS.get(permission)
    if not column:
        return None
    with _lock, _conn() as c:
        c.execute(
            f"UPDATE admins SET {column}=%s WHERE id=%s",
            (bool(enabled), admin_id)
        )
        if c.rowcount != 1:
            return None
    return get_admin_permissions(admin_id)


# ── Gateway change approval queue ─────────────────────────────────────────

def create_gateway_change_request(admin_id: int, admin_name: str, admin_role: str,
                                  scope: str, before_cfg: dict,
                                  proposed_cfg: dict) -> int:
    """Queue a sensitive gateway change without touching the live config."""
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO gateway_change_requests "
            "(admin_id, admin_name, admin_role, scope, before_cfg, proposed_cfg, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (admin_id, admin_name, admin_role, scope,
             json.dumps(before_cfg, ensure_ascii=False),
             json.dumps(proposed_cfg, ensure_ascii=False), _now())
        )
        return c.fetchone()['id']


def get_pending_gateway_change_requests() -> list:
    with _conn() as c:
        c.execute(
            "SELECT * FROM gateway_change_requests WHERE status='pending' "
            "ORDER BY created_at ASC"
        )
        rows = c.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['before_cfg'] = json.loads(item['before_cfg'])
        item['proposed_cfg'] = json.loads(item['proposed_cfg'])
        item['created_label'] = fmt_ts(item['created_at'])
        result.append(item)
    return result


def get_gateway_change_request(request_id: int) -> dict | None:
    with _conn() as c:
        c.execute("SELECT * FROM gateway_change_requests WHERE id=%s", (request_id,))
        row = c.fetchone()
    if not row:
        return None
    item = dict(row)
    item['before_cfg'] = json.loads(item['before_cfg'])
    item['proposed_cfg'] = json.loads(item['proposed_cfg'])
    item['created_label'] = fmt_ts(item['created_at'])
    return item


def decide_gateway_change_request(request_id: int, status: str,
                                  decided_by: str, note: str = None) -> dict | None:
    if status not in ('approved', 'rejected'):
        raise ValueError('Status de decisão inválido')
    with _lock, _conn() as c:
        c.execute(
            "UPDATE gateway_change_requests SET status=%s, decided_at=%s, "
            "decided_by=%s, decision_note=%s "
            "WHERE id=%s AND status='pending' RETURNING *",
            (status, _now(), decided_by, note, request_id)
        )
        row = c.fetchone()
    if not row:
        return None
    item = dict(row)
    item['before_cfg'] = json.loads(item['before_cfg'])
    item['proposed_cfg'] = json.loads(item['proposed_cfg'])
    return item


# ── Activity log ──────────────────────────────────────────────────────────

def _owner_is_invisible(admin_id: int) -> bool:
    with _conn() as c:
        c.execute(
            "SELECT status FROM admins WHERE id=%s AND role='owner'", (admin_id,)
        )
        row = c.fetchone()
    return bool(row and row['status'] == 'invisible')


def log_action(admin_id, admin_name: str, role: str,
               action: str, detail: str = None, ip: str = None):
    if role == 'owner' and _owner_is_invisible(admin_id):
        return
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO activity_log(admin_id, admin_name, role, action, detail, ip, ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (admin_id, admin_name, role, action, detail, ip, _now())
        )


def get_activity_log(limit: int = 300, viewer_role: str = 'owner'):
    with _conn() as c:
        if viewer_role == 'owner':
            c.execute(
                "SELECT * FROM activity_log ORDER BY ts DESC LIMIT %s", (limit,)
            )
        else:
            # Non-owners: hide manually-suppressed entries AND any private-room
            # deletion records (owner-only, confidential by design).
            c.execute(
                "SELECT * FROM activity_log "
                "WHERE hidden_by IS NULL "
                "  AND action NOT LIKE '%%conversa privada%%' "
                "ORDER BY ts DESC LIMIT %s",
                (limit,)
            )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def hide_log_entry(entry_id: int, hider_id: int):
    with _lock, _conn() as c:
        c.execute(
            "UPDATE activity_log SET hidden_by=%s, hidden_at=%s WHERE id=%s",
            (hider_id, _now(), entry_id)
        )


def get_log_entry(entry_id: int):
    with _conn() as c:
        c.execute("SELECT * FROM activity_log WHERE id=%s", (entry_id,))
        row = c.fetchone()
    return dict(row) if row else None


def delete_log_entries(entry_ids: list):
    if not entry_ids:
        return
    with _lock, _conn() as c:
        placeholders = ','.join(['%s'] * len(entry_ids))
        c.execute(f"DELETE FROM activity_log WHERE id IN ({placeholders})", list(entry_ids))


def clear_all_logs():
    with _lock, _conn() as c:
        c.execute("DELETE FROM activity_log")


# ── Audit log ─────────────────────────────────────────────────────────────

def audit(admin_id, admin_name: str, role: str, action: str,
          detail: str = None, before_val: str = None, after_val: str = None, ip: str = None):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO audit_log(admin_id, admin_name, role, action, detail, before_val, after_val, ip, ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (admin_id, admin_name, role, action, detail, before_val, after_val, ip, _now())
        )
    for hook in _audit_hooks:
        try:
            hook(admin_name, role, action, detail, ip)
        except Exception:
            pass


def get_audit_log(limit: int = 500):
    with _conn() as c:
        c.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC LIMIT %s", (limit,)
        )
        rows = c.fetchall()
    return [dict(r) for r in rows]


# ── Notifications ─────────────────────────────────────────────────────────

def add_notification(type_: str, message: str, min_role: str = 'admin'):
    if type_ == 'security':
        min_role = 'owner'
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO notifications(type, message, ts, read_by, min_role) VALUES(%s,%s,%s,%s,%s)",
            (type_, message, _now(), '[]', min_role)
        )


def get_gateway_config() -> dict | None:
    """Fetch the full gateway config JSON from admin_settings (cloud-persisted).
    Returns None if not yet stored (first run)."""
    try:
        with _conn() as c:
            c.execute("SELECT value FROM admin_settings WHERE key='gateway_config_json'")
            row = c.fetchone()
        if row:
            return json.loads(row['value'])
    except Exception:
        pass
    return None


def set_gateway_config(cfg: dict):
    """Persist gateway config JSON to admin_settings so it survives container restarts."""
    try:
        with _lock, _conn() as c:
            c.execute(
                "INSERT INTO admin_settings(key, value) VALUES('gateway_config_json',%s) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(cfg, ensure_ascii=False),)
            )
    except Exception:
        pass


# ── Dashboard metrics (replaces dashboard_config.json) ─────────────────────

def get_metric(key: str, default: float = 0) -> float:
    with _conn() as c:
        c.execute("SELECT metric_value FROM dashboard_metrics WHERE metric_key=%s", (key,))
        row = c.fetchone()
    return float(row['metric_value']) if row else default


def get_all_metrics() -> dict:
    with _conn() as c:
        c.execute("SELECT metric_key, metric_value FROM dashboard_metrics")
        rows = c.fetchall()
    return {r['metric_key']: r['metric_value'] for r in rows}


def set_metric(key: str, value: float):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO dashboard_metrics(metric_key, metric_value) VALUES(%s,%s) "
            "ON CONFLICT(metric_key) DO UPDATE SET metric_value=excluded.metric_value",
            (key, value)
        )


def increment_metric(key: str, amount: float = 1) -> float:
    """Atomically increments a metric row and returns the new value."""
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO dashboard_metrics(metric_key, metric_value) VALUES(%s,%s) "
            "ON CONFLICT(metric_key) DO UPDATE SET "
            "metric_value=dashboard_metrics.metric_value+excluded.metric_value "
            "RETURNING metric_value",
            (key, amount)
        )
        row = c.fetchone()
    return float(row['metric_value']) if row else amount


def delete_metric(key: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM dashboard_metrics WHERE metric_key=%s", (key,))


# ── Failed URLs (replaces failed_urls.json) ─────────────────────────────────

def log_failed_url_db(url: str, error: str, error_type: str, domain: str, now: str):
    with _lock, _conn() as c:
        c.execute("SELECT count FROM failed_urls WHERE url=%s", (url,))
        row = c.fetchone()
        if row:
            c.execute(
                "UPDATE failed_urls SET count=count+1, last_seen=%s, error=%s, error_type=%s WHERE url=%s",
                (now, error, error_type, url)
            )
        else:
            c.execute(
                "INSERT INTO failed_urls(url, domain, error, error_type, first_seen, last_seen, count, resolved) "
                "VALUES(%s,%s,%s,%s,%s,%s,1,FALSE)",
                (url, domain, error, error_type, now, now)
            )


def get_failed_urls() -> list[dict]:
    with _conn() as c:
        c.execute("SELECT * FROM failed_urls")
        rows = c.fetchall()
    return [dict(r) for r in rows]


def mark_failed_url_resolved(url: str, resolved: bool = True) -> bool:
    with _lock, _conn() as c:
        c.execute(
            "UPDATE failed_urls SET resolved=%s WHERE url=%s",
            (resolved, url)
        )
        return c.rowcount > 0


def mark_failed_domain_resolved(domain: str) -> int:
    with _lock, _conn() as c:
        c.execute(
            "UPDATE failed_urls SET resolved=TRUE WHERE domain=%s AND resolved=FALSE",
            (domain,)
        )
        return c.rowcount


def delete_failed_url(url: str) -> bool:
    """Explicit SQL DELETE — the URL never reappears after a reboot."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM failed_urls WHERE url=%s", (url,))
        return c.rowcount > 0


def delete_failed_domain(domain: str) -> int:
    with _lock, _conn() as c:
        c.execute("DELETE FROM failed_urls WHERE domain=%s", (domain,))
        return c.rowcount


def delete_resolved_failed_urls() -> int:
    with _lock, _conn() as c:
        c.execute("DELETE FROM failed_urls WHERE resolved=TRUE")
        return c.rowcount


def clear_all_failed_urls():
    with _lock, _conn() as c:
        c.execute("DELETE FROM failed_urls")


def _notif_visible(notif_dict: dict, viewer_role: str,
                   manage_gateways: bool = False) -> bool:
    d = notif_dict
    if d.get('type') == 'security' and viewer_role != 'owner':
        return False
    # Notificações financeiras ('payment') → Owner sempre vê; qualquer membro
    # com a permissão individual de gateway também pode vê-las.
    if d.get('type') == 'payment':
        if viewer_role == 'owner':
            return True
        if manage_gateways:
            return True
        return False
    min_role = d.get('min_role') or 'admin'
    viewer_level = ROLE_LEVEL.get(viewer_role, 99)
    min_level    = ROLE_LEVEL.get(min_role, 1)
    return viewer_level <= min_level


def get_notifications(limit: int = 20, viewer_role: str = 'owner',
                      admin_id=None, unread_only: bool = False,
                      manage_gateways: bool = False):
    with _conn() as c:
        c.execute(
            "SELECT * FROM notifications ORDER BY ts DESC LIMIT %s", (limit,)
        )
        rows = c.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if not _notif_visible(d, viewer_role, manage_gateways):
            continue
        # Skip notifications the user has individually cleared
        if admin_id is not None:
            cleared = json.loads(d.get('cleared_by') or '[]')
            if admin_id in cleared:
                continue
        if unread_only and admin_id is not None:
            readers = json.loads(d.get('read_by') or '[]')
            if admin_id in readers:
                continue
        result.append(d)
    return result


def mark_notification_read(notif_id: int, admin_id: int):
    with _lock, _conn() as c:
        c.execute("SELECT read_by FROM notifications WHERE id=%s", (notif_id,))
        row = c.fetchone()
        if row:
            readers = json.loads(row['read_by'] or '[]')
            if admin_id not in readers:
                readers.append(admin_id)
            c.execute("UPDATE notifications SET read_by=%s WHERE id=%s",
                      (json.dumps(readers), notif_id))


def count_unread_notifications(admin_id: int, viewer_role: str = 'owner',
                               manage_gateways: bool = False) -> int:
    with _conn() as c:
        c.execute("SELECT type, read_by, min_role, cleared_by FROM notifications")
        rows = c.fetchall()
    count = 0
    for r in rows:
        d = dict(r)
        if not _notif_visible(d, viewer_role, manage_gateways):
            continue
        # Notifications cleared by this admin do not count
        cleared = json.loads(d.get('cleared_by') or '[]')
        if admin_id in cleared:
            continue
        readers = json.loads(d.get('read_by') or '[]')
        if admin_id not in readers:
            count += 1
    return count


def mark_all_notifications_read(admin_id: int, viewer_role: str = 'helper'):
    notifications = get_notifications(limit=200, viewer_role=viewer_role)
    for n in notifications:
        mark_notification_read(n['id'], admin_id)


def clear_notifications(admin_id: int, viewer_role: str = 'helper'):
    """Mark all visible notifications as cleared for this specific admin_id only.
    Does NOT delete notifications globally — each team member manages their own panel."""
    # Use a very high limit so we never leave older notifications uncleared
    visible = get_notifications(limit=9999, viewer_role=viewer_role, admin_id=admin_id)
    with _lock, _conn() as c:
        for n in visible:
            row = c.execute("SELECT cleared_by FROM notifications WHERE id=%s", (n['id'],)).fetchone()
            if not row:
                continue
            try:
                cleared = json.loads(row[0] or '[]')
            except Exception:
                cleared = []
            if admin_id not in cleared:
                cleared.append(admin_id)
                c.execute("UPDATE notifications SET cleared_by=%s WHERE id=%s",
                          (json.dumps(cleared), n['id']))


# ── Reports ───────────────────────────────────────────────────────────────

def create_report(from_id: int, from_name: str, from_role: str,
                  to_role: str, message: str):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO reports(from_id, from_name, from_role, to_role, message, ts) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (from_id, from_name, from_role, to_role, message, _now())
        )


def get_reports(viewer_role: str, viewer_id: int = None, limit: int = 100):
    with _conn() as c:
        if viewer_role == 'owner':
            c.execute(
                "SELECT * FROM reports ORDER BY ts DESC LIMIT %s", (limit,)
            )
        else:
            c.execute(
                "SELECT * FROM reports WHERE to_role=%s OR from_id=%s ORDER BY ts DESC LIMIT %s",
                (viewer_role, viewer_id, limit)
            )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def delete_report(report_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM reports WHERE id=%s", (report_id,))


def get_report_by_id(report_id: int):
    with _conn() as c:
        c.execute("SELECT * FROM reports WHERE id=%s", (report_id,))
        row = c.fetchone()
    return dict(row) if row else None


def resolve_report(report_id: int, resolved_by: str):
    with _lock, _conn() as c:
        c.execute(
            "UPDATE reports SET resolved=1, resolved_by=%s, resolved_at=%s WHERE id=%s",
            (resolved_by, _now(), report_id)
        )


# ── Maintenance mode ──────────────────────────────────────────────────────

def get_maintenance():
    with _conn() as c:
        c.execute("SELECT * FROM maintenance_mode WHERE id=1")
        row = c.fetchone()
    return dict(row) if row else {'enabled': 0, 'message': 'Site em manutenção.'}


def set_maintenance(enabled: bool, message: str, set_by: str):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO maintenance_mode(id, enabled, message, set_by, set_at) VALUES(1,%s,%s,%s,%s) "
            "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, message=excluded.message, "
            "set_by=excluded.set_by, set_at=excluded.set_at",
            (1 if enabled else 0, message, set_by, _now())
        )


# ── Gateway backups ───────────────────────────────────────────────────────

def save_gateway_backup(config_dict: dict, saved_by: str):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO gateway_backups(config, saved_by, ts) VALUES(%s,%s,%s)",
            (json.dumps(config_dict, ensure_ascii=False), saved_by, _now())
        )


def get_gateway_backups(limit: int = 10):
    with _conn() as c:
        c.execute(
            "SELECT * FROM gateway_backups ORDER BY ts DESC LIMIT %s", (limit,)
        )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def delete_gateway_backup(backup_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM gateway_backups WHERE id=%s", (backup_id,))


# ── Anti-abuse detection ──────────────────────────────────────────────────

ABUSE_WINDOW_SEC  = 180
ABUSE_THRESHOLD   = 20

def check_abuse(admin_id: int) -> int:
    since = _now(datetime.now() - timedelta(seconds=ABUSE_WINDOW_SEC))
    critical_actions = (
        'Sessão encerrada', 'Admin excluído', 'Admin criado', 'Cargo alterado',
        'Gateway salvo', 'Log apagado', 'URL excluída do log'
    )
    placeholders = ','.join(['%s'] * len(critical_actions))
    with _conn() as c:
        c.execute(
            f"SELECT COUNT(*) AS n FROM activity_log WHERE admin_id=%s AND ts>=%s AND action IN ({placeholders})",
            (admin_id, since) + critical_actions
        )
        row = c.fetchone()
    return row['n'] if row else 0


# ── Permissions ───────────────────────────────────────────────────────────

def has_perm(role: str, perm: str) -> bool:
    perms = PERMISSIONS.get(role, set())
    return 'all' in perms or perm in perms


def can_manage(actor_role: str, target_role: str) -> bool:
    if actor_role == 'owner':
        return target_role in ('admin', 'moderator', 'helper')
    if actor_role == 'admin':
        return target_role in ('moderator', 'helper')
    return False


def can_terminate_session(actor_role: str, target_role: str) -> bool:
    if actor_role == 'owner':
        return True
    if actor_role == 'admin':
        return target_role in ('moderator', 'helper')
    return False


# ── Team chat ─────────────────────────────────────────────────────────────

def _ensure_chat_table():
    with _lock, _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS team_chat (
            id         SERIAL PRIMARY KEY,
            admin_id   INTEGER NOT NULL,
            admin_name TEXT NOT NULL,
            role       TEXT NOT NULL,
            avatar     TEXT NOT NULL DEFAULT 'av1',
            message    TEXT NOT NULL,
            ts         TEXT NOT NULL,
            channel    TEXT NOT NULL DEFAULT 'team',
            room_id    INTEGER DEFAULT NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id         SERIAL PRIMARY KEY,
            p1_id      INTEGER NOT NULL,
            p1_name    TEXT NOT NULL,
            p2_id      INTEGER NOT NULL,
            p2_name    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            closed     INTEGER NOT NULL DEFAULT 0,
            closed_at  TEXT,
            closed_by  TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS team_chat_seen (
            key          TEXT NOT NULL,
            admin_id     INTEGER NOT NULL,
            last_seen_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (key, admin_id)
        )
        """)
        for col, coldef in [
            ('channel', "TEXT NOT NULL DEFAULT 'team'"),
            ('room_id', "INTEGER DEFAULT NULL"),
        ]:
            c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='team_chat' AND column_name=%s AND table_schema='public'",
                (col,)
            )
            if not c.fetchone():
                c.execute(f"ALTER TABLE team_chat ADD COLUMN {col} {coldef}")


def send_chat(admin_id: int, admin_name: str, role: str, avatar: str, message: str, channel: str = 'team'):
    _ensure_chat_table()
    channel = channel if channel in ('team', 'internal') else 'team'
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO team_chat(admin_id, admin_name, role, avatar, message, ts, channel) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (admin_id, admin_name, role, avatar, message[:500], _now(), channel)
        )


def get_chat_messages(limit: int = 100, since_id: int = 0, channel: str = 'team'):
    _ensure_chat_table()
    channel = channel if channel in ('team', 'internal') else 'team'
    with _conn() as c:
        if since_id:
            c.execute(
                "SELECT * FROM team_chat WHERE id > %s AND channel=%s AND room_id IS NULL ORDER BY ts ASC LIMIT %s",
                (since_id, channel, limit)
            )
        else:
            c.execute(
                "SELECT * FROM team_chat WHERE channel=%s AND room_id IS NULL ORDER BY ts DESC LIMIT %s",
                (channel, limit)
            )
        rows = c.fetchall()
    if not since_id:
        rows = list(reversed(rows))
    return [dict(r) for r in rows]


# ── Private Chat Rooms ────────────────────────────────────────────────────

def create_room(p1_id: int, p1_name: str, p2_id: int, p2_name: str) -> int:
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO chat_rooms(p1_id, p1_name, p2_id, p2_name, created_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",
            (p1_id, p1_name, p2_id, p2_name, _now())
        )
        room_id = c.fetchone()['id']
        c.execute(
            "INSERT INTO team_chat(admin_id, admin_name, role, avatar, message, ts, channel, room_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (0, 'Sistema', 'owner', 'av1',
             f'💬 Conversa iniciada entre {p1_name} e {p2_name}', _now(), 'room', room_id)
        )
    return room_id


def get_room(room_id: int) -> dict | None:
    _ensure_chat_table()
    with _conn() as c:
        c.execute("SELECT * FROM chat_rooms WHERE id=%s", (room_id,))
        row = c.fetchone()
    return dict(row) if row else None


def get_rooms_for_admin(admin_id: int):
    _ensure_chat_table()
    with _conn() as c:
        c.execute(
            "SELECT * FROM chat_rooms WHERE p1_id=%s OR p2_id=%s ORDER BY id DESC",
            (admin_id, admin_id)
        )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def get_all_rooms():
    _ensure_chat_table()
    with _conn() as c:
        c.execute("SELECT * FROM chat_rooms ORDER BY id DESC")
        rows = c.fetchall()
    return [dict(r) for r in rows]


def close_room(room_id: int, closed_by: str):
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute(
            "UPDATE chat_rooms SET closed=1, closed_at=%s, closed_by=%s WHERE id=%s",
            (_now(), closed_by, room_id)
        )
        c.execute(
            "INSERT INTO team_chat(admin_id, admin_name, role, avatar, message, ts, channel, room_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (0, 'Sistema', 'owner', 'av1',
             f'🔴 Conversa encerrada por {closed_by}', _now(), 'room', room_id)
        )


def is_room_participant(room_id: int, admin_id: int) -> bool:
    _ensure_chat_table()
    with _conn() as c:
        c.execute(
            "SELECT id FROM chat_rooms WHERE id=%s AND (p1_id=%s OR p2_id=%s)",
            (room_id, admin_id, admin_id)
        )
        row = c.fetchone()
    return row is not None


def get_room_other_participant_status(room_id: int, viewer_id: int) -> str | None:
    """Status ('online'/'busy'/'invisible'/'offline') of the other participant in a
    private room, respecting Owner invisibility for non-owner viewers."""
    room = get_room(room_id)
    if not room:
        return None
    other_id = room['p2_id'] if room['p1_id'] == viewer_id else room['p1_id']
    other = get_admin_by_id(other_id)
    if not other:
        return None
    viewer = get_admin_by_id(viewer_id)
    viewer_role = viewer['role'] if viewer else None
    if other['status'] == 'invisible' and other['role'] == 'owner' and viewer_role != 'owner':
        return 'offline'
    return other['status']


def send_room_message(room_id: int, admin_id: int, admin_name: str, role: str, avatar: str, message: str):
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO team_chat(admin_id, admin_name, role, avatar, message, ts, channel, room_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (admin_id, admin_name, role, avatar, message[:500], _now(), 'room', room_id)
        )


def get_room_messages(room_id: int, limit: int = 100, since_id: int = 0):
    _ensure_chat_table()
    with _conn() as c:
        if since_id:
            c.execute(
                "SELECT * FROM team_chat WHERE room_id=%s AND id > %s ORDER BY ts ASC LIMIT %s",
                (room_id, since_id, limit)
            )
        else:
            c.execute(
                "SELECT * FROM team_chat WHERE room_id=%s ORDER BY ts ASC LIMIT %s",
                (room_id, limit)
            )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def get_room_last_message(room_id: int) -> dict | None:
    with _conn() as c:
        c.execute(
            "SELECT * FROM team_chat WHERE room_id=%s ORDER BY id DESC LIMIT 1",
            (room_id,)
        )
        row = c.fetchone()
    return dict(row) if row else None


def delete_room(room_id: int):
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute("DELETE FROM team_chat WHERE room_id=%s", (room_id,))
        c.execute("DELETE FROM team_chat_seen WHERE key=%s", (f'room_{room_id}',))
        c.execute("DELETE FROM chat_rooms WHERE id=%s", (room_id,))


def delete_room_message(room_id: int, msg_id: int, admin_id: int, is_owner: bool) -> bool:
    """Delete a single non-system message from a private room.
    Owners may delete any message; participants can only delete their own."""
    _ensure_chat_table()
    with _lock, _conn() as c:
        if is_owner:
            c.execute(
                "DELETE FROM team_chat WHERE id=%s AND room_id=%s AND admin_id != 0",
                (msg_id, room_id)
            )
        else:
            c.execute(
                "DELETE FROM team_chat WHERE id=%s AND room_id=%s AND admin_id=%s",
                (msg_id, room_id, admin_id)
            )
        return c.rowcount > 0


def get_internal_access_moderator() -> bool:
    _ensure_chat_table()
    with _conn() as c:
        c.execute("SELECT value FROM admin_settings WHERE key='internal_access_mod'")
        row = c.fetchone()
    return (row['value'] == '1') if row else True


def set_internal_access_moderator(enabled: bool):
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO admin_settings(key, value) VALUES('internal_access_mod',%s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ('1' if enabled else '0',)
        )


def get_internal_access_helper() -> bool:
    _ensure_chat_table()
    with _conn() as c:
        c.execute("SELECT value FROM admin_settings WHERE key='internal_access_helper'")
        row = c.fetchone()
    return (row['value'] == '1') if row else False


def set_internal_access_helper(enabled: bool):
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO admin_settings(key, value) VALUES('internal_access_helper',%s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ('1' if enabled else '0',)
        )


def get_version_override() -> str | None:
    """Retorna versão customizada salva pelo admin, ou None se não definida."""
    with _conn() as c:
        c.execute("SELECT value FROM admin_settings WHERE key='version_override'")
        row = c.fetchone()
    return row['value'] if row and row['value'] else None


def set_version_override(version: str | None):
    """Salva (ou remove) versão customizada. Passa None para limpar."""
    with _lock, _conn() as c:
        if version:
            c.execute(
                "INSERT INTO admin_settings(key, value) VALUES('version_override',%s) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (version,)
            )
        else:
            c.execute("DELETE FROM admin_settings WHERE key='version_override'")


def clear_channel_messages(channel: str):
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute("DELETE FROM team_chat WHERE channel=%s AND room_id IS NULL", (channel,))


# ── Typing indicators (in-memory, no DB) ─────────────────────────────────

def set_typing(key: str, admin_id: int, admin_name: str):
    now = time.time()
    if key not in _typing:
        _typing[key] = {}
    _typing[key][admin_id] = {'name': admin_name, 'expires': now + 5.0}


def get_typing(key: str, admin_id: int) -> list:
    now    = time.time()
    bucket = _typing.get(key, {})
    result  = []
    expired = []
    for aid, info in bucket.items():
        if info['expires'] < now:
            expired.append(aid)
            continue
        if aid != admin_id:
            result.append(info['name'])
    for aid in expired:
        bucket.pop(aid, None)
    return result


# ── Read receipts ─────────────────────────────────────────────────────────

def update_last_seen(key: str, admin_id: int, last_seen_id: int):
    _ensure_chat_table()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO team_chat_seen(key, admin_id, last_seen_id) VALUES(%s,%s,%s) "
            "ON CONFLICT(key, admin_id) DO UPDATE "
            "SET last_seen_id=GREATEST(excluded.last_seen_id, team_chat_seen.last_seen_id)",
            (key, admin_id, last_seen_id)
        )


def get_others_seen_up_to(key: str, admin_id: int) -> int:
    _ensure_chat_table()
    with _conn() as c:
        c.execute(
            "SELECT MAX(last_seen_id) AS max_id FROM team_chat_seen WHERE key=%s AND admin_id != %s",
            (key, admin_id)
        )
        row = c.fetchone()
    return (row['max_id'] or 0) if row else 0


# ── PIX Payments ──────────────────────────────────────────────────────────

def save_pix_payment(payment_id: str, gateway_id: str, amount: float, expires_at: str = None,
                     nickname: str = None, avatar_id: str = None, donor_token: str = None,
                     font_id: str = 'default', text_effect: str = 'solid',
                     name_color: str = '#f3f4f6', currency: str = 'BRL'):
    """nickname/avatar_id/donor_token são opcionais — usados apenas nos fluxos de
    cartão (Stripe/PayPal) onde o apelido é coletado ANTES do redirect, já que o
    navegador sai do site e pode não voltar à mesma aba. Isso permite que
    fulfill_pix_payment() finalize o cadastro no ranking automaticamente quando
    o gateway confirmar o pagamento via webhook."""
    if expires_at is None:
        expires_at = _now(datetime.now() + timedelta(minutes=10))
    currency = str(currency or 'BRL').upper()
    if currency not in {'BRL', 'USD'}:
        currency = 'BRL'
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO pix_payments(payment_id, gateway_id, amount, currency, status, ts, expires_at, "
            "nickname, avatar_id, donor_token, font_id, text_effect, name_color) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (payment_id, gateway_id, amount, currency, 'pending', _now(), expires_at,
             (nickname[:40] if nickname else None), avatar_id, donor_token,
             font_id if font_id in {'default', 'inter', 'orbitron', 'rajdhani',
                                     'space-grotesk', 'press-start', 'bangers',
                                     'roboto-mono', 'montserrat', 'gothic', 'script',
                                     'pixel'} else 'default',
              text_effect if text_effect in {'solid', 'gradient', 'neon', 'pop', 'gummy', 'prism'} else 'solid',
              name_color.lower() if re.fullmatch(r'#[0-9a-fA-F]{6}', str(name_color or '')) else '#f3f4f6')
        )


def get_pix_payments(limit: int = 50):
    with _conn() as c:
        c.execute(
            "SELECT * FROM pix_payments ORDER BY ts DESC LIMIT %s", (limit,)
        )
        rows = c.fetchall()
    return [dict(r) for r in rows]


def get_pix_payment_by_id(payment_id: str):
    with _conn() as c:
        c.execute("SELECT * FROM pix_payments WHERE payment_id=%s", (payment_id,))
        row = c.fetchone()
    return dict(row) if row else None


def auto_expire_pix_payment(payment_id: str) -> str:
    """Check expiry, auto-update to 'expired' if needed. Returns current status string."""
    with _lock, _conn() as c:
        c.execute("SELECT status, expires_at FROM pix_payments WHERE payment_id=%s", (payment_id,))
        row = c.fetchone()
        if not row:
            return 'not_found'
        status     = row['status']
        expires_at = row['expires_at']
        if status == 'pending' and expires_at:
            try:
                if datetime.now() > datetime.fromisoformat(str(expires_at)):
                    c.execute(
                        "UPDATE pix_payments SET status='expired', updated_at=%s WHERE payment_id=%s",
                        (_now(), payment_id)
                    )
                    return 'expired'
            except Exception:
                pass
        return status


def update_pix_payment_status(payment_id: str, status: str, updated_by: str):
    status = status if status in ('pending', 'paid', 'approved', 'cancelled', 'expired') else 'pending'
    with _lock, _conn() as c:
        c.execute(
            "UPDATE pix_payments SET status=%s, updated_at=%s, updated_by=%s WHERE payment_id=%s",
            (status, _now(), updated_by, payment_id)
        )


def cancel_pix_payment(payment_id: str, updated_by: str = 'supporter') -> str:
    """Cancel a pending PIX order when the supporter changes the amount.

    A client can request cancellation, but it can never cancel a payment that
    was already approved/paid. This protects the confirmation flow when a
    supporter clicks the back button after paying.
    """
    with _lock, _conn() as c:
        c.execute(
            "SELECT status, expires_at FROM pix_payments WHERE payment_id=%s",
            (payment_id,)
        )
        row = c.fetchone()
        if not row:
            return 'not_found'

        status = row['status']
        if status in ('paid', 'approved', 'expired', 'cancelled'):
            return status

        expires_at = row['expires_at']
        if expires_at:
            try:
                if datetime.now() > datetime.fromisoformat(str(expires_at)):
                    c.execute(
                        "UPDATE pix_payments SET status='expired', updated_at=%s, updated_by=%s "
                        "WHERE payment_id=%s AND status='pending'",
                        (_now(), updated_by, payment_id)
                    )
                    return 'expired'
            except Exception:
                pass

        c.execute(
            "UPDATE pix_payments SET status='cancelled', updated_at=%s, updated_by=%s "
            "WHERE payment_id=%s AND status='pending'",
            (_now(), updated_by, payment_id)
        )
        return 'cancelled' if c.rowcount else 'pending'


def fulfill_pix_payment(payment_id: str, updated_by: str) -> dict | None:
    """Handler global de fulfillment (Item 3): marca o pagamento como 'paid' e,
    se um nickname/avatar já foi capturado para ele (fluxos de cartão que
    coletam o apelido ANTES do redirect), completa automaticamente o cadastro
    no ranking — sem depender do usuário voltar à aba original.

    Chamado por TODOS os pontos de confirmação de pagamento: webhook do
    Mercado Pago, webhook do Stripe, captura de ordem do PayPal e aprovação
    manual de PIX pelo admin. Isso garante que a geração de tag/discriminador
    e a promoção de posição no ranking aconteçam de forma uniforme,
    independente do gateway usado.

    Retorna o dict de register_donation() (para o broadcast do ticker) quando
    um cadastro novo/atualizado ocorreu aqui, ou None quando não havia
    nickname pendente (ex.: fluxo PIX clássico, onde o cadastro é concluído
    pelo próprio cliente via /api/donation/register) ou o pagamento já tinha
    sido vinculado a um ranking antes.
    """
    with _lock, _conn() as c:
        c.execute("SELECT status FROM pix_payments WHERE payment_id=%s", (payment_id,))
        row = c.fetchone()
        if not row:
            return None
        if row['status'] != 'paid':
            c.execute(
                "UPDATE pix_payments SET status='paid', updated_at=%s, updated_by=%s WHERE payment_id=%s",
                (_now(), updated_by, payment_id)
            )

    payment_row = get_pix_payment_by_id(payment_id)
    if not payment_row:
        return None
    if payment_row.get('linked_ranking_token'):
        return None  # já vinculado a um cadastro de ranking — nada a fazer
    nickname = (payment_row.get('nickname') or '').strip()
    if not nickname:
        return None  # sem apelido pré-coletado — aguarda o fluxo client-side
    avatar_id = payment_row.get('avatar_id') or 'm_1'
    font_id = payment_row.get('font_id') or 'default'
    text_effect = payment_row.get('text_effect') or 'solid'
    name_color = payment_row.get('name_color') or '#f3f4f6'
    token = payment_row.get('donor_token') or str(_uuid.uuid4())
    amount = float(payment_row.get('amount', 0))
    try:
        result = register_donation(nickname, avatar_id, amount, token,
                                    payment_id=payment_id, font_id=font_id,
                                    text_effect=text_effect, name_color=name_color,
                                    currency=payment_row.get('currency', 'BRL'))
        result['avatar_id'] = avatar_id
        result['amount'] = amount
        return result
    except DuplicatePaymentError:
        return None


def expire_all_pending_pix() -> int:
    """Bulk-expire all pending PIX payments that are past their expires_at.
    Called by a background thread every 60s. Returns number of records expired."""
    now_str = _now()
    with _lock, _conn() as c:
        c.execute(
            "UPDATE pix_payments SET status='expired', updated_at=%s "
            "WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < %s",
            (now_str, now_str)
        )
        return c.rowcount if hasattr(c, 'rowcount') else 0


def delete_pix_payment(payment_id: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM pix_payments WHERE payment_id=%s", (payment_id,))


# ── Webhooks ──────────────────────────────────────────────────────────────

_audit_hooks: list = []


def register_audit_hook(fn):
    _audit_hooks.append(fn)


def get_webhooks() -> list:
    with _conn() as c:
        c.execute("SELECT * FROM webhooks ORDER BY id")
        rows = c.fetchall()
    return [dict(r) for r in rows]


def get_webhooks_for_event(event_type: str) -> list:
    result = []
    for wh in get_webhooks():
        if not wh['enabled']:
            continue
        try:
            events = json.loads(wh['events'])
        except Exception:
            events = []
        if event_type in events:
            result.append(wh)
    return result


def add_webhook(name: str, url: str, events: list) -> int:
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO webhooks(name, url, enabled, events, created_at) VALUES(%s,%s,1,%s,%s) RETURNING id",
            (name, url, json.dumps(events), _now())
        )
        return c.fetchone()['id']


def update_webhook(wh_id: int, name: str, url: str, enabled: bool, events: list):
    with _lock, _conn() as c:
        c.execute(
            "UPDATE webhooks SET name=%s, url=%s, enabled=%s, events=%s WHERE id=%s",
            (name, url, 1 if enabled else 0, json.dumps(events), wh_id)
        )


def get_crowned_admin_id() -> int | None:
    with _conn() as c:
        c.execute("SELECT id FROM admins WHERE crowned=1 LIMIT 1")
        row = c.fetchone()
    return row['id'] if row else None


def toggle_crown(target_id: int) -> bool:
    with _lock, _conn() as c:
        c.execute("SELECT crowned FROM admins WHERE id=%s", (target_id,))
        row = c.fetchone()
        if not row:
            return False
        if row['crowned']:
            c.execute("UPDATE admins SET crowned=0 WHERE id=%s", (target_id,))
            return False
        else:
            c.execute("UPDATE admins SET crowned=0")
            c.execute("UPDATE admins SET crowned=1 WHERE id=%s", (target_id,))
            return True


def toggle_webhook(wh_id: int) -> bool:
    with _lock, _conn() as c:
        c.execute("SELECT enabled FROM webhooks WHERE id=%s", (wh_id,))
        row = c.fetchone()
        if not row:
            return False
        new_state = 0 if row['enabled'] else 1
        c.execute("UPDATE webhooks SET enabled=%s WHERE id=%s", (new_state, wh_id))
        return bool(new_state)


# ── IP Lockout unlock requests ─────────────────────────────────────────────

def create_unlock_request(ip: str, ua: str, geo: str, attempted_username: str = None, hardware_info: str = None) -> int | None:
    with _lock, _conn() as c:
        c.execute(
            "SELECT id FROM lockout_unlock_requests WHERE ip=%s AND status='pending'", (ip,)
        )
        if c.fetchone():
            return None
        c.execute(
            "INSERT INTO lockout_unlock_requests (ip, ua, geo, ts, attempted_username, hardware_info) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (ip, ua[:300] if ua else '', geo[:300] if geo else '', _now(), attempted_username, hardware_info)
        )
        row = c.fetchone()
        return row['id'] if row else None


def get_unlock_requests(status: str = 'pending', limit: int = 50):
    with _conn() as c:
        c.execute(
            "SELECT * FROM lockout_unlock_requests WHERE status=%s ORDER BY ts DESC LIMIT %s",
            (status, limit)
        )
        return [dict(r) for r in c.fetchall()]


def count_pending_unlock_requests() -> int:
    with _conn() as c:
        c.execute("SELECT COUNT(*) AS n FROM lockout_unlock_requests WHERE status='pending'")
        row = c.fetchone()
        return row['n'] if row else 0


def delete_unlock_request(req_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM lockout_unlock_requests WHERE id=%s", (req_id,))


def handle_unlock_request(req_id: int, action: str, handled_by: str) -> dict | None:
    with _lock, _conn() as c:
        c.execute("SELECT * FROM lockout_unlock_requests WHERE id=%s", (req_id,))
        req = c.fetchone()
        if not req:
            return None
        c.execute(
            "UPDATE lockout_unlock_requests SET status=%s, handled_by=%s, handled_at=%s WHERE id=%s",
            (action, handled_by, _now(), req_id)
        )
        if action == 'approved':
            c.execute("DELETE FROM ip_lockouts WHERE ip=%s", (req['ip'],))
        return dict(req)


def delete_webhook(wh_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM webhooks WHERE id=%s", (wh_id,))
