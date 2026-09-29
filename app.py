import io
import os
import re
import json
import copy
import logging
import threading
import time
import random
import bs4  # BeautifulSoup
import requests
from requests.exceptions import ChunkedEncodingError as _ChunkedErr
try:
    from urllib3.exceptions import ProtocolError as _ProtocolErr, IncompleteRead as _IncompleteRead
except ImportError:
    _ProtocolErr = OSError
    _IncompleteRead = OSError
import urllib.parse
from flask import Flask, jsonify, render_template, request, flash, Response, redirect, request, abort, send_file, session, g, make_response
from media_scraper import MediaScraper
from urllib.parse import urlparse
import debug_logger
import admin_store
import donor_avatars
import discord_webhook as dwh
import stats_store
import payment_gateway as pgw
import math
from datetime import datetime, timedelta
import uuid
import colorlog

__version__ = "2.2.1"

# Fonts exposed to supporters are a server-side allowlist. The same ids are
# used by the donation form, the ranking JSON and the CSS preview.
DONOR_FONTS = {
    'default':     {'label': 'Padrão', 'css': "'Segoe UI', sans-serif"},
    'inter':       {'label': 'Inter', 'css': "'Inter', sans-serif"},
    'orbitron':    {'label': 'Orbitron', 'css': "'Orbitron', sans-serif"},
    'rajdhani':    {'label': 'Rajdhani', 'css': "'Rajdhani', sans-serif"},
    'space-grotesk': {'label': 'Space Grotesk', 'css': "'Space Grotesk', sans-serif"},
    'press-start': {'label': 'Press Start 2P', 'css': "'Press Start 2P', monospace"},
    'bangers':     {'label': 'Bangers', 'css': "'Bangers', cursive"},
    'roboto-mono': {'label': 'Roboto Mono', 'css': "'Roboto Mono', monospace"},
    'montserrat':  {'label': 'Montserrat', 'css': "'Montserrat', sans-serif"},
    'gothic':      {'label': 'Gótico', 'css': "'UnifrakturCook', serif"},
    'script':      {'label': 'Script', 'css': "'Caveat', cursive"},
    'pixel':       {'label': 'Pixel', 'css': "'Rubik Mono One', sans-serif"},
}

DONOR_EFFECTS = {
    'solid':    'Sólido',
    'gradient': 'Gradiente',
    'neon':     'Neon',
    'pop':      'Pop',
    'gummy':    'Gummy',
    'prism':    'Prism',
}

DONOR_COLORS = {
    '#f3f4f6', '#00ff88', '#00f0ff', '#a855f7', '#ff2d95',
    '#ffd700', '#ff7f50', '#22c55e', '#3b82f6', '#ec4899', '#f97316',
}


def _clean_donor_style(data: dict) -> dict:
    """Normalize supporter style input before it reaches the data store."""
    font_id = str(data.get('font_id', 'default')).strip().lower()
    text_effect = str(data.get('text_effect', 'solid')).strip().lower()
    name_color = str(data.get('name_color', '#f3f4f6')).strip().lower()
    if font_id not in DONOR_FONTS:
        font_id = 'default'
    if text_effect not in DONOR_EFFECTS:
        text_effect = 'solid'
    if not re.fullmatch(r'#[0-9a-f]{6}', name_color):
        name_color = '#f3f4f6'
    return {'font_id': font_id, 'text_effect': text_effect, 'name_color': name_color}

# IMPORT DO ROBÔ DA ORACLE POSICIONADO NO INÍCIO DO ARQUIVO
import oracle_bot

# Configurar o handler colorido
handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    '%(log_color)s%(asctime)s %(name)-129s %(levelname)-8s %(message)s',
    datefmt='%H:%M:%S',
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    }
))

# Configurar o logger principal
logger = logging.getLogger()
logger.setLevel(logging.INFO)  # Mudamos para INFO para reduzir verbosidade
logger.addHandler(handler)

# Configurar loggers específicos
urllib3_logger = logging.getLogger('urllib3.connectionpool')
urllib3_logger.setLevel(logging.WARNING)  # Reduzir logs de conexão HTTP

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.INFO)

# Logger personalizado para a aplicação
app_logger = logging.getLogger('MediaScraper')
app_logger.setLevel(logging.INFO)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Initialize media scraper
scraper = MediaScraper()

# ==============================================================================
# ── ROTAS DO ORACLEBOT (INTEGRADAS AO SISTEMA DE ADM DO SEU SITE) ─────────────────
# ==============================================================================

@app.route('/admin/oracle')
def painel_oracle_admin():
    session_id = request.cookies.get('admin_session')
    
    # Validação adaptiva: tenta encontrar o método correto de sessão no seu banco local
    is_valid = False
    if session_id:
        if hasattr(admin_store, 'check_session') and admin_store.check_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'get_session') and admin_store.get_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'is_admin') and admin_store.is_admin(session_id):
            is_valid = True
            
    if not is_valid:
        return abort(403) # Bloqueia acesso não autorizado de forma severa
        
    return render_template('oracle_panel.html')


@app.route('/admin/oracle-logs-api')
def oracle_logs_api():
    session_id = request.cookies.get('admin_session')
    
    is_valid = False
    if session_id:
        if hasattr(admin_store, 'check_session') and admin_store.check_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'get_session') and admin_store.get_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'is_admin') and admin_store.is_admin(session_id):
            is_valid = True

    if not is_valid:
        return jsonify({"erro": "Não autorizado"}), 403
        
    return jsonify({"logs": oracle_bot.pegar_logs()})

@app.route('/admin/oracle-reboot', methods=['POST'])
def oracle_reboot():
    session_id = request.cookies.get('admin_session')
    
    is_valid = False
    if session_id:
        if hasattr(admin_store, 'check_session') and admin_store.check_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'get_session') and admin_store.get_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'is_admin') and admin_store.is_admin(session_id):
            is_valid = True

    if not is_valid:
        return jsonify({"erro": "Não autorizado"}), 403
        
    import oracle_bot
    oracle_bot.reiniciar_bot_oracle()
    return jsonify({"status": "rebooted"})

@app.route('/admin/oracle-start', methods=['POST'])
def oracle_start():
    session_id = request.cookies.get('admin_session')
    
    # Mesma validação de segurança que você já usa
    is_valid = False
    if session_id:
        if hasattr(admin_store, 'check_session') and admin_store.check_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'get_session') and admin_store.get_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'is_admin') and admin_store.is_admin(session_id):
            is_valid = True

    if not is_valid:
        return jsonify({"erro": "Não autorizado"}), 403
        
    # Chama a função que você já tem para ligar o bot
    oracle_bot.ligar_bot_oracle()
    return jsonify({"status": "started"})


@app.route('/admin/oracle-stop', methods=['POST'])
def oracle_stop():
    session_id = request.cookies.get('admin_session')
    
    # Mantém exatamente a mesma validação de segurança que você já usa nas outras rotas
    is_valid = False
    if session_id:
        if hasattr(admin_store, 'check_session') and admin_store.check_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'get_session') and admin_store.get_session(session_id):
            is_valid = True
        elif hasattr(admin_store, 'is_admin') and admin_store.is_admin(session_id):
            is_valid = True

    if not is_valid:
        return jsonify({"erro": "Não autorizado"}), 403
        
    # Chama a função de parada que está dentro do seu oracle_bot.py
    import oracle_bot
    oracle_bot.parar_bot_oracle()
    return jsonify({"status": "stopped"})

# ==============================================================================

# ── Admin auth system ─────────────────────────────────────────────────────────
ADMIN_COOKIE = 'admin_session'
# Executado em thread daemon para não bloquear a inicialização do Flask em
# ambientes com latência DB alta (cold start no Hugging Face / Supabase).
threading.Thread(target=admin_store.init_db, daemon=True, name='admin-init-db').start()
donor_avatars.ensure_donor_avatars()

# ── Suspicious URL attack detection ───────────────────────────────────────────
_SUSPICIOUS_PATTERNS = [
    (re.compile(r'(\.\./|\.\.\\)', re.I),                                          'Path Traversal'),
    (re.compile(r'(<script|javascript:|onerror\s*=|onload\s*=|alert\s*\(|eval\s*\(|document\.cookie|<iframe)', re.I), 'XSS'),
    (re.compile(r"(union\s+select|select\s+.+\s+from|drop\s+table|insert\s+into|delete\s+from|update\s+.+\s+set|'?\s*or\s+'?1'?\s*=\s*'?1|--\s*$|;\s*--)", re.I), 'SQL Injection'),
    (re.compile(r'(/etc/passwd|/proc/self|/var/www|\.env|\.git/config|wp-config)', re.I), 'Path Traversal'),
]

# ── WAF: patterns that trigger an automatic IP ban (not just a log entry) ──────
# Each entry: (compiled_regex, short_code, human_reason_pt)
_WAF_BAN_PATTERNS = [
    (re.compile(r'\.env(\.(production|local|backup|dev|staging|test|example))?(\b|$|[?#])', re.I),
     'Credential Exfiltration (.env)',
     'Exfiltração de Credenciais — Acesso forçado a arquivos de configuração sensíveis (.env / segredos do sistema)'),

    (re.compile(r'(\.\./|\.\.\\|%2e%2e%2f|%2e%2e/|\.\.%2f)', re.I),
     'Path Traversal',
     'Varredura de Path Traversal — Tentativa de acesso não autorizado a diretórios e arquivos confidenciais do sistema'),

    (re.compile(r'(wp-config\.php|/etc/passwd|/proc/self|/var/www|\.git/config|\.htpasswd|\.bash_history|/etc/shadow)', re.I),
     'System File Probe',
     'Sondagem de Arquivos do Sistema — Tentativa de leitura de arquivos críticos do servidor (senhas, configurações internas)'),

    (re.compile(r'[?&]file=(/|%2f|\.\.|%2e%2e)', re.I),
     'LFI via file= parameter',
     'Inclusão Local de Arquivo (LFI) — Exploração do parâmetro file= para carregar arquivos arbitrários do servidor'),

    (re.compile(r'(phpMyAdmin|phpmyadmin|PMA_|/mysql/|/adminer\.php|/db\.php)', re.I),
     'DB Admin Scanner',
     'Scanner de Painel de Banco de Dados — Sondagem automatizada em busca de interfaces de administração expostas'),

    (re.compile(r'(\beval\b.*\(|base64_decode\s*\(|exec\s*\(|system\s*\(|passthru\s*\()', re.I),
     'Remote Code Execution',
     'Tentativa de Execução Remota de Código (RCE) — Injeção de payload para execução arbitrária de comandos no servidor'),
]

def _render_waf_block(ip: str, reason: str, ts: str, url_path: str,
                      realtime: bool = False) -> 'Response':
    """Return the WAF block screen response (403).

    realtime=True  → caught in the act by the live filter right now.
    realtime=False → IP was already on the ban list (historical ban).
    """
    from datetime import datetime as _dt
    try:
        dt = _dt.fromisoformat(ts)
        ts_fmt = dt.strftime('%d/%m/%Y às %H:%M:%S UTC')
    except Exception:
        ts_fmt = ts
    return make_response(
        render_template('waf_block.html',
                        attacker_ip=ip,
                        threat_type=reason,
                        blocked_ts=ts_fmt,
                        blocked_url=url_path,
                        realtime=realtime),
        403
    )

# ── Pre-seeded WAF bans (known attackers with documented attack history) ──────
def _seed_waf_bans():
    """Insert known malicious IPs into the WAF ban list at startup.
    Uses INSERT … ON CONFLICT DO NOTHING so it never overwrites a manual unban."""
    _KNOWN_BANS = [
        (
            '20.125.48.163',
            'Tentativa de exploração de vulnerabilidade via Path Traversal — '
            'Acesso não autorizado a diretórios e arquivos confidenciais do sistema',
            '/seed-historical',
        ),
        (
            '52.240.186.21',
            'Tentativa de vazamento de credenciais e arquivos de configuração sensíveis — '
            'Acesso forçado a arquivos .env (segredos e configurações internas do sistema)',
            '/.env',
        ),
    ]
    try:
        from admin_store import _conn, _now, _lock
        with _lock, _conn() as c:
            for ip, reason, url in _KNOWN_BANS:
                c.execute("""
                    INSERT INTO waf_bans (ip, reason, url_path, method, ua, ts)
                    VALUES (%s, %s, %s, 'GET', 'seed/historical', %s)
                    ON CONFLICT (ip) DO NOTHING
                """, (ip, reason[:255], url, _now()))
    except Exception as _e:
        app_logger.warning('WAF seed skipped: %s', _e)

_seed_waf_bans()

# Register audit hook → fires 'admin_action' webhooks for every audit() call
admin_store.register_audit_hook(
    lambda name, role, action, detail, ip:
        dwh.fire('admin_action', dwh.build_action_embed(name, role, action, detail, ip))
)

# ── Global chat context (injects chat vars into every admin template) ──────────
@app.context_processor
def inject_chat_context():
    admin = getattr(g, 'admin', None)
    if not admin:
        return {}
    try:
        chat_admins = [
            {'id': a['id'], 'username': a['username'],
             'display_name': a['display_name'], 'avatar': a['avatar'], 'role': a['role']}
            for a in admin_store.list_admins()
            if a['id'] != admin['id'] and a['active']
        ]
        return {
            'chat_admins': chat_admins,
            'internal_access_mod': admin_store.get_internal_access_moderator(),
            'internal_access_helper': admin_store.get_internal_access_helper(),
            'role_meta': admin_store.ROLE_META,
            'has_gateway_access': admin_store.has_admin_permission(admin, 'manageGateways'),
        }
    except Exception:
        return {}

# ── Jinja2 filters ────────────────────────────────────────────────────────────
@app.template_filter('fromjson')
def fromjson_filter(s):
    """Parse a JSON string to a Python dict/list for use in templates."""
    try:
        import json as _json
        return _json.loads(s) if s else {}
    except Exception:
        return {}

@app.template_filter('fmt_ts')
def fmt_ts_filter(ts):
    """Format ISO timestamp to human-readable: 03/06/2026 12:06:45"""
    return admin_store.fmt_ts(ts)

@app.template_filter('fmt_ts_short')
def fmt_ts_short_filter(ts):
    """Format ISO timestamp short: 03/06/2026 às 12:06"""
    if not ts:
        return '—'
    try:
        from datetime import datetime as _dt
        dt = _dt.fromisoformat(ts)
        return dt.strftime('%d/%m/%Y às %H:%M')
    except Exception:
        return ts


# ── Geo helpers ────────────────────────────────────────────────────────────────
def _cc_flag(cc: str) -> str:
    if len(cc) == 2:
        return chr(ord(cc[0]) + 0x1F1A5) + chr(ord(cc[1]) + 0x1F1A5)
    return '🌐'


def _get_geo(ip: str) -> dict:
    """Fetch geolocation + OSINT for an IP, trying multiple providers with fallback.
    Extended fields: isp, asn, is_proxy (VPN/proxy/Tor), is_hosting (datacenter)."""
    _empty = {'city': '?', 'region': '?', 'country': '?', 'country_code': '',
              'org': '', 'lat': '', 'lon': '', 'flag': '🌐',
              'isp': '', 'asn': '', 'is_proxy': False, 'is_hosting': False}

    def _ipapi_co():
        r = requests.get(f'https://ipapi.co/{ip}/json/', timeout=3)
        if not r.ok:
            return None
        gd = r.json()
        if gd.get('error'):
            return None
        cc  = (gd.get('country_code') or '').upper()
        org = gd.get('org') or ''
        asn = gd.get('asn') or ''
        return {'city': gd.get('city') or '?', 'region': gd.get('region') or '?',
                'country': gd.get('country_name') or '?', 'country_code': cc,
                'org': org, 'flag': _cc_flag(cc),
                'lat': str(gd.get('latitude') or ''), 'lon': str(gd.get('longitude') or ''),
                'isp': org, 'asn': asn, 'is_proxy': False, 'is_hosting': False}

    def _ip_api_com():
        # Full OSINT fields: ISP, ASN, proxy/VPN flag, datacenter/hosting flag
        r = requests.get(
            f'http://ip-api.com/json/{ip}'
            f'?fields=status,city,regionName,country,countryCode,org,isp,as,proxy,hosting,mobile,lat,lon',
            timeout=3)
        if not r.ok:
            return None
        gd = r.json()
        if gd.get('status') != 'success':
            return None
        cc = (gd.get('countryCode') or '').upper()
        return {'city': gd.get('city') or '?', 'region': gd.get('regionName') or '?',
                'country': gd.get('country') or '?', 'country_code': cc,
                'org': gd.get('org') or '', 'flag': _cc_flag(cc),
                'lat': str(gd.get('lat') or ''), 'lon': str(gd.get('lon') or ''),
                'isp':        gd.get('isp') or gd.get('org') or '',
                'asn':        gd.get('as') or '',
                'is_proxy':   bool(gd.get('proxy')),
                'is_hosting': bool(gd.get('hosting'))}

    def _ipinfo_io():
        r = requests.get(f'https://ipinfo.io/{ip}/json', timeout=3)
        if not r.ok:
            return None
        gd = r.json()
        cc  = (gd.get('country') or '').upper()
        loc = (gd.get('loc') or ',').split(',')
        org = gd.get('org') or ''
        # ipinfo.io org is formatted as "AS15169 Google LLC"
        asn = org.split()[0] if org and org.startswith('AS') else ''
        return {'city': gd.get('city') or '?', 'region': gd.get('region') or '?',
                'country': cc or '?', 'country_code': cc,
                'org': org, 'flag': _cc_flag(cc),
                'lat': loc[0] if len(loc) > 0 else '', 'lon': loc[1] if len(loc) > 1 else '',
                'isp': org, 'asn': asn, 'is_proxy': False, 'is_hosting': False}

    for fn in (_ipapi_co, _ip_api_com, _ipinfo_io):
        try:
            result = fn()
            if result and result.get('city') and result['city'] != '?':
                return result
        except Exception:
            continue
    return _empty


def _notif_link(type_: str, message: str) -> str:
    """Map a notification to the most relevant admin panel URL."""
    m = message.lower()
    if 'desbloqueio' in m or 'unlock' in m:
        return '/admin/unlock-requests'
    if type_ == 'report' or 'relatório' in m or 'relatorio' in m:
        return '/admin/reports'
    if type_ == 'security' or 'brute force' in m or 'bloqueado' in m or 'suspeito' in m or 'ataque' in m:
        return '/admin/security'
    if 'gateway' in m or 'pagamento' in m or 'pix' in m:
        return '/payment/admin'
    if 'manutenção' in m or 'manutencao' in m:
        return '/admin/'
    return '/admin/notifications'


def _get_admin():
    """Return (admin_dict, session_dict) from the session cookie, or (None, None)."""
    token = request.cookies.get(ADMIN_COOKIE, '').strip()
    if not token or len(token) != 64:
        return None, None
    return admin_store.get_session(token)


def _developer_admin():
    """Return the authenticated admin only when developer mode is enabled for
    this exact admin session. The signed Flask session is only a state hint;
    the admin cookie is always revalidated against the database."""
    admin, _ = _get_admin()
    if not admin or not admin.get('active', True):
        return None
    if not admin_store.has_admin_permission(admin, 'accessDevMode'):
        return None
    if session.get('developer_mode_admin_id') != admin.get('id'):
        return None
    if not session.get('developer_mode_enabled', False):
        return None
    return admin


def _developer_ui_state():
    """Return homepage developer-control visibility and current switch state.

    Turning the mode off revokes only the current session's developer
    capabilities; it does not remove the control from an authorized admin.
    """
    admin, _ = _get_admin()
    if not admin or not admin.get('active', True):
        return None, False, False
    if not admin_store.has_admin_permission(admin, 'accessDevMode'):
        return None, False, False
    enabled = _developer_admin() is not None
    return admin, True, enabled

def _get_client_ip():
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() if xff else (request.remote_addr or '127.0.0.1')


def _staff_device_signature(canvas_fp: str) -> str:
    """Derive a server-keyed device identifier without storing raw fingerprint data.

    The browser-generated canvas value is only an input to an HMAC.  Using a
    keyed SHA-256 instead of a plain hash prevents someone who sees a stored
    whitelist value from reproducing the identifier without the server secret.
    """
    import hashlib as _hashlib
    import hmac as _hmac

    value = str(canvas_fp or '').strip()[:128]
    if not value:
        return ''
    secret = app.secret_key
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    elif not isinstance(secret, bytes):
        secret = str(secret).encode('utf-8')
    payload = ('staff-waf-device-v2:' + value).encode('utf-8')
    return _hmac.new(secret, payload, _hashlib.sha256).hexdigest()


# Paths that bypass the admin auth guard (login pages + public batch report)
_NO_AUTH_PATHS = frozenset({
    '/admin/login',
    '/admin/login/request-unlock',
    '/admin/login/report-hardware',   # locked clients send fingerprint without a session
    '/admin/logout',
    '/debug/login',
    '/debug/logout',
    '/debug/report-batch',
    '/debug-info',
    '/debug-security-screen',         # WAF block screen preview (access gated inside the view)
})

@app.before_request
def _waf_enforce():
    """WAF Layer 1 — runs before everything else.

    1. If the IP is already banned, immediately serve the block screen.
    2. If the request matches a critical pattern, ban the IP and serve the
       block screen. The ban is persisted to the DB so it survives restarts.
    """
    path = request.path
    # Never block: static assets, the keep-alive ping, and the debug preview
    if (path.startswith('/static') or path == '/api/ping'
            or path == '/debug-security-screen'):
        return

    ip = _get_client_ip()

    # ── Step 1: already banned? ───────────────────────────────────────────
    try:
        ban = admin_store.is_ip_banned_waf(ip)
        if ban:
            # Check whitelist first — if this IP/device is a known dev/pentester, skip block.
            # Derive the same keyed SHA-256 device signature used at login time so we can match
            # the stored hash without ever persisting the raw canvas fingerprint.
            canvas_fp = request.headers.get('X-Canvas-FP', '').strip()
            device_sig = _staff_device_signature(canvas_fp)
            if admin_store.is_ip_whitelisted(ip, device_sig):
                pass  # Whitelisted — skip block
            else:
                return _render_waf_block(ip, ban['reason'], ban['ts'], ban['url_path'])
    except Exception:
        pass  # DB not ready yet — fail open, do not block legitimate traffic

    # ── Step 2: scan for ban-worthy patterns (two-strikes system) ────────
    qs = request.query_string.decode('utf-8', errors='ignore')
    full_url = f'{path}?{qs}' if qs else path
    ua_raw   = request.headers.get('User-Agent', '')

    for pattern, short_code, human_reason in _WAF_BAN_PATTERNS:
        if pattern.search(full_url):
            _is_second_strike = False
            try:
                # Always log the suspicious request
                admin_store.record_suspicious_request(
                    ip, f'WAF-WARN: {short_code}', full_url[:500], request.method,
                    ua=ua_raw, browser_name=_parse_browser(ua_raw),
                    operating_system=_parse_os(ua_raw))

                warn = admin_store.get_waf_warning(ip)
                if warn and warn['count'] >= 1:
                    # ── 2nd strike → permanent ban ─────────────────────────
                    admin_store.ban_ip_waf(ip, human_reason, full_url[:500],
                                           request.method, ua_raw)
                    admin_store.clear_waf_warning(ip)
                    app_logger.warning(
                        '🚫 WAF BAN (2ª tentativa) — IP=%s  reason=%s  url=%s',
                        ip, short_code, full_url[:120])
                    _is_second_strike = True
                else:
                    # ── 1st strike → warn only, return 404 ────────────────
                    admin_store.record_waf_warning(ip, human_reason,
                                                   full_url[:500],
                                                   request.method, ua_raw)
                    app_logger.warning(
                        '⚠️  WAF ALERTA (1ª tentativa) — IP=%s  reason=%s  url=%s',
                        ip, short_code, full_url[:120])
            except Exception:
                pass

            if _is_second_strike:
                from datetime import datetime as _dt
                ts_now = _dt.utcnow().isoformat(timespec='seconds')
                return _render_waf_block(ip, human_reason, ts_now, full_url[:500],
                                         realtime=True)
            # First strike: return a plain 404 — no block screen yet
            return make_response('Not Found', 404)


@app.before_request
def _detect_suspicious_request():
    """Detect malicious URL patterns: path traversal, XSS, SQL injection."""
    path = request.path
    # Skip static files and the keep-alive ping — all other paths are inspected
    if path.startswith('/static') or path == '/api/ping':
        return
    full_url = path
    qs = request.query_string.decode('utf-8', errors='ignore')
    if qs:
        full_url = f'{path}?{qs}'
    for pattern, attack_type in _SUSPICIOUS_PATTERNS:
        if pattern.search(full_url):
            try:
                ip = _get_client_ip()
                ua_raw = request.headers.get('User-Agent', '')
                admin_store.record_suspicious_request(
                    ip, attack_type, full_url[:500], request.method,
                    ua=ua_raw, browser_name=_parse_browser(ua_raw), operating_system=_parse_os(ua_raw)
                )
            except Exception:
                pass
            break


@app.before_request
def _maintenance_check():
    """Block non-admin users when maintenance mode is on."""
    path = request.path
    # Static files and admin/login always pass
    if path.startswith('/static') or path.startswith('/admin') or path.startswith('/debug'):
        return
    if path in ('/', '/scrape', '/batch', '/batch/stream', '/batch/config',
                '/ping', '/dashboard', '/dashboard/api', '/payment/gateways',
                '/payment/intent', '/payment/create', '/force-download',
                '/proxy-media', '/validate_url', '/debug-info', '/debug/report-batch',
                '/payment/disabled-status'):
        try:
            maint = admin_store.get_maintenance()
            if maint.get('enabled'):
                return render_template('maintenance.html', message=maint.get('message', '')), 503
        except Exception:
            pass


_SENSITIVE_PERM_PATHS = {
    '/admin/audit':         'manage_admins',
    '/admin/sessions':      'manage_admins',
    '/admin/maintenance':   'manage_admins',
    '/admin/gateway':       'gateway',
    '/payment/admin':       'gateway',
}

def _fire_access_alert(admin: dict, path: str, required_perm: str) -> None:
    """Cria notificação para o owner quando um admin tenta acessar rota sem permissão."""
    try:
        role_label = admin.get('role', '?')
        username   = admin.get('username', '?')
        msg = (f'[Acesso negado] {username} ({role_label}) tentou acessar '
               f'{path} (requer: {required_perm})')
        admin_store.add_notification('security', msg)
    except Exception:
        pass

@app.before_request
def _guard_admin_areas():
    """
    Central security gate for all /admin/* and /debug/* paths.
    Returns HTTP 404 (not 403) for unauthenticated requests to hide the
    existence of these routes from scanners and parameter-injection attacks.
    Auth is always cookie-based — query-string or header injection is ignored.
    """
    path = request.path
    protected = (
        path.startswith('/admin') or
        path.startswith('/debug') or
        path.startswith('/payment/admin') or
        path in ('/dashboard/config', '/dashboard/config/reset', '/batch/panel')
    )
    if not protected:
        return
    if path in _NO_AUTH_PATHS:
        return
    admin, _ = _get_admin()
    if not admin:
        abort(404)
    if not admin.get('active', True):
        abort(404)
    g.admin = admin
    # Auto-set online for any authenticated admin actively using the panel
    if admin.get('status') == 'offline':
        admin_store.set_status(admin['id'], 'online')
        admin['status'] = 'online'
    for prefix, perm in _SENSITIVE_PERM_PATHS.items():
        if path.startswith(prefix):
            allowed = (
                admin_store.has_admin_permission(admin, 'manageGateways')
                if perm == 'gateway'
                else admin_store.has_perm(admin['role'], perm)
            )
            if not allowed:
                _fire_access_alert(admin, path, perm)
                abort(404)
            break

# ─────────────────────────────────────────────────────────────────────────────

@app.template_filter('url_encode')
def url_encode_filter(s):
    return urllib.parse.quote_plus(s)
  

@app.route('/proxy-media')
def proxy_media():
    """Faz proxy de mídia para preview inline no browser (sem forçar download)."""
    import html as _html
    import threading as _threading

    raw_url = request.args.get('url', '')

    # ── Attack Blinding Gate ─────────────────────────────────────────────────
    # Covers XSS, SQL injection, and automated-scanner probes (blind SQLi,
    # time-based delays, RCE probes).  Logs to forensics silently, then drops
    # the request immediately — the upstream fetch is NEVER attempted.
    _ATTACK_RE = re.compile(
        r'(<script|javascript\s*:|onerror\s*=|onload\s*=|alert\s*\(|eval\s*\(|'
        r'document\.cookie|<iframe|vbscript\s*:|data\s*:\s*text/html|'       # XSS
        r'select\s+.{0,80}from[\s(]|union[\s(]+select|dbms_pipe|pg_sleep|'   # SQLi
        r'sleep\s*\(\s*\d|waitfor\s+delay|exec\s*\(|xp_cmdshell)',           # SQLi/RCE
        re.I,
    )
    if _ATTACK_RE.search(raw_url):
        # Capture request context before spawning background thread (Flask
        # request context is not available inside daemon threads).
        _atk_ip      = request.remote_addr or 'unknown'
        _atk_ua      = request.headers.get('User-Agent', '')
        _atk_payload = raw_url[:500]
        _atk_type    = (
            'SQL Injection'
            if re.search(
                r'(select\s+.{0,80}from[\s(]|union[\s(]+select|dbms_pipe|'
                r'pg_sleep|sleep\s*\(\s*\d|waitfor\s+delay|xp_cmdshell)',
                raw_url, re.I,
            )
            else 'XSS'
        )

        def _log_attack_attempt():
            """Silent async forensic write — zero terminal output."""
            try:
                admin_store.record_suspicious_request(
                    _atk_ip, _atk_type, _atk_payload, 'GET'
                )
                app_logger.debug(
                    'ATTACK_BLOCKED type=%s ip=%s ua=%.80s payload=%.200s',
                    _atk_type, _atk_ip, _atk_ua, _atk_payload,
                )
            except Exception:
                pass

        _threading.Thread(target=_log_attack_attempt, daemon=True).start()
        # Hard execution stop — attacker receives an immediate 400; no upstream
        # request is ever made, so no "No connection adapters" / "Invalid URL"
        # noise ever reaches the server console.
        return jsonify({"success": False, "error": "Requisição bloqueada por motivos de segurança cibernética."}), 400
    # ── End Attack Gate ──────────────────────────────────────────────────────

    url = raw_url
    if not url:
        return "Parâmetro 'url' é obrigatório.", 400

    # html.escape() the raw input for any context where it would be reflected
    # back in an HTML response body (error strings below).  The unescaped `url`
    # variable is used only as the outgoing HTTP fetch target.
    safe_url_display = _html.escape(url)

    try:
        parsed = urlparse(url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        referer = "https://erome.com/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": referer.rstrip("/"),
    }

    try:
        r = requests.get(url, headers=headers, stream=True, timeout=(10, 60))
        r.raise_for_status()

        content_type = r.headers.get('Content-Type', 'application/octet-stream')
        response_headers = {
            'Content-Type': content_type,
            'Accept-Ranges': 'bytes',
        }
        if 'Content-Length' in r.headers:
            response_headers['Content-Length'] = r.headers['Content-Length']
        if 'Content-Range' in r.headers:
            response_headers['Content-Range'] = r.headers['Content-Range']

        return Response(
            r.iter_content(chunk_size=65536),
            status=r.status_code,
            headers=response_headers
        )
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status not in (404, 410):
            debug_logger.log_failed_url(url, f"HTTP {status}", "http_error")
        return f"Erro ao carregar mídia: HTTP {_html.escape(str(status))}", 502
    except requests.exceptions.ConnectionError:
        debug_logger.log_failed_url(url, "Erro de conexão", "connection_error")
        return "Erro de conexão ao carregar mídia.", 502
    except requests.exceptions.Timeout:
        debug_logger.log_failed_url(url, "Timeout", "timeout")
        return "Timeout ao carregar mídia.", 504
    except requests.RequestException as e:
        debug_logger.log_failed_url(url, str(e), "request_error")
        return f"Erro ao carregar mídia: {_html.escape(str(e))}", 502


@app.route('/force-download')
def force_download():
    url = request.args.get('url')
    if not url:
        return "Parâmetro 'url' é obrigatório.", 400

    try:
        parsed_dl = urlparse(url)
        referer_dl = f"{parsed_dl.scheme}://{parsed_dl.netloc}/"
    except Exception:
        referer_dl = "https://erome.com/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer_dl,
        "Origin": referer_dl.rstrip("/"),
    }

    # Keep the download name safe for Content-Disposition and avoid carrying
    # query strings or header-control characters from an untrusted URL.
    filename = os.path.basename(urlparse(url).path) or 'download'
    filename = re.sub(r'[\r\n"\\]', '_', filename)[:180] or 'download'
    upstream = None
    try:
        # Do not buffer or persist media on the Hugging Face container. The
        # origin response remains attached to the Flask generator below.
        upstream = requests.get(
            url,
            stream=True,
            timeout=(10, 60),
            headers=headers,
        )
        upstream.raise_for_status()
        content_type = upstream.headers.get('Content-Type', 'application/octet-stream')
        expected_length = upstream.headers.get('Content-Length')

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=1024 * 32):
                    if chunk:
                        yield chunk
            except (_ChunkedErr, _ProtocolErr, _IncompleteRead):
                # The client receives a clean end-of-stream instead of a
                # ChunkedEncodingError escaping through Gunicorn.
                return
            finally:
                upstream.close()

        download_headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Transfer-Encoding': 'chunked',
            'Cache-Control': 'no-cache',
        }
        if expected_length and expected_length.isdigit():
            download_headers['X-Download-Expected-Length'] = expected_length

        stats_store.increment('downloads')
        return Response(generate(), mimetype=content_type, headers=download_headers)
    except requests.exceptions.HTTPError as e:
        if upstream is not None:
            upstream.close()
        status = e.response.status_code if e.response is not None else 502
        if status not in (404, 410):
            debug_logger.log_failed_url(url, f"HTTP {status}", "http_error")
        return f"Erro ao baixar o arquivo: HTTP {status}", status if isinstance(status, int) else 502
    except (_ChunkedErr, _ProtocolErr, _IncompleteRead, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        if upstream is not None:
            upstream.close()
        debug_logger.log_failed_url(url, str(e), "download_error")
        return "Erro de conexão ao iniciar o download.", 502
    except requests.RequestException as e:
        if upstream is not None:
            upstream.close()
        debug_logger.log_failed_url(url, str(e), "request_error")
        return "Erro ao baixar o arquivo.", 502
    except Exception as e:
        if upstream is not None:
            upstream.close()
        debug_logger.log_failed_url(url, str(e), "download_error")
        return "Erro inesperado ao baixar o arquivo.", 500

@app.route('/')
def index():
    """Página principal com formulário para inserir URL"""
    stats_store.increment('views')
    payments_disabled = pgw.are_payments_disabled()
    developer_admin, developer_available, developer_enabled = _developer_ui_state()
    
    _VIP_DEFAULTS = [
        {'tier_id': 0, 'tier_name': 'VIP SUPREME',  'min_value': 500, 'bonus_urls': 100},
        {'tier_id': 1, 'tier_name': 'VIP DIAMANTE',  'min_value': 100, 'bonus_urls': 60},
        {'tier_id': 2, 'tier_name': 'VIP RUBI',      'min_value': 80,  'bonus_urls': 50},
        {'tier_id': 3, 'tier_name': 'VIP OURO',      'min_value': 60,  'bonus_urls': 40},
        {'tier_id': 4, 'tier_name': 'VIP PRATA',     'min_value': 35,  'bonus_urls': 30},
        {'tier_id': 5, 'tier_name': 'VIP BRONZE',    'min_value': 15,  'bonus_urls': 20},
        {'tier_id': 6, 'tier_name': 'VIP ESTELAR',   'min_value': 1,   'bonus_urls': 10},
    ]
    
    try:
        vip_tiers = admin_store.get_vip_tiers() or _VIP_DEFAULTS
        if len(vip_tiers) < 7:
            vip_tiers = _VIP_DEFAULTS
    except Exception:
        vip_tiers = _VIP_DEFAULTS
        
    _tm = {t['tier_id']: t['bonus_urls'] for t in vip_tiers}

    # 1. PEGA O USUÁRIO LOGADO E ATUALIZA SEU NIVEL VIP EM TEMPO REAL PARA O CABEÇALHO
    user_data = None
    if hasattr(g, 'user') and g.user:
        # Cria uma cópia mutável para evitar travas de leitura no objeto do banco
        user_data = dict(g.user)
        total = float(user_data.get('total_donated') or 0.0)
        
        if total >= 500.00: user_data['vip_level'] = 0
        elif total >= 100.00: user_data['vip_level'] = 1
        elif total >= 80.00:  user_data['vip_level'] = 2
        elif total >= 60.00:  user_data['vip_level'] = 3
        elif total >= 35.00:  user_data['vip_level'] = 4
        elif total >= 15.00:  user_data['vip_level'] = 5
        elif total >= 1.00:   user_data['vip_level'] = 6
        else: user_data['vip_level'] = 6

    # 2. PEGA O RANKING DO BANCO E FORÇA O DESCONGELAMENTO DE TODOS OS BADGES DO MENUS LATERAL
    raw_ranking = []
    try:
        raw_ranking = admin_store.get_ranking() or []
    except Exception:
        pass

    updated_ranking = []
    for r in raw_ranking:
        r_dict = dict(r)
        total_donated = float(r_dict.get('total_donated') or 0.0)
        
        if total_donated >= 500.00: r_dict['vip_level'] = 0
        elif total_donated >= 100.00: r_dict['vip_level'] = 1
        elif total_donated >= 80.00:  r_dict['vip_level'] = 2
        elif total_donated >= 60.00:  r_dict['vip_level'] = 3
        elif total_donated >= 35.00:  r_dict['vip_level'] = 4
        elif total_donated >= 15.00:  r_dict['vip_level'] = 5
        else: r_dict['vip_level'] = 6  # VIP ESTELAR para doações de R$ 1 até R$ 14.99
        
        updated_ranking.append(r_dict)

    return render_template(
        'index.html',
        user=user_data,  # Injeta o usuário com o nível VIP corrigido em tempo real
        ranking_rows=updated_ranking,  # Envia a lista corrigida para o frontend
        payments_disabled=payments_disabled,
        vip_tiers=vip_tiers,
        vip0_links=_tm.get(0, 100),
        vip1_links=_tm.get(1, 60),
        vip2_links=_tm.get(2, 50),
        vip3_links=_tm.get(3, 40),
        vip4_links=_tm.get(4, 30),
        vip5_links=_tm.get(5, 20),
        vip6_links=_tm.get(6, 10),
        developer_admin=developer_admin,
        developer_available=developer_available,
        developer_enabled=developer_enabled,
        developer_role_label=(
            admin_store.ROLE_META.get(developer_admin.get('role'), {}).get('label')
            if developer_admin else None
        ),
    )


@app.route('/api/developer-mode/toggle', methods=['POST'])
def developer_mode_toggle():
    """Enable/disable the homepage developer mode for the current admin session."""
    admin, _ = _get_admin()
    if not admin or not admin.get('active', True):
        return jsonify({'error': 'Sessão administrativa inválida'}), 403
    if not admin_store.has_admin_permission(admin, 'accessDevMode'):
        return jsonify({'error': 'Você não possui acesso ao Modo Desenvolvedor'}), 403

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))
    if enabled:
        # Older sessions may still carry the former revocation marker. It is
        # obsolete now that the authorized control remains visible when off.
        session.pop('developer_mode_revoked_admin_id', None)
        session['developer_mode_admin_id'] = admin['id']
        session['developer_mode_enabled'] = True
        state = True
    else:
        session.pop('developer_mode_enabled', None)
        session.pop('developer_mode_admin_id', None)
        session.pop('developer_mode_revoked_admin_id', None)
        state = False

    try:
        admin_store.audit(
            admin['id'], admin['display_name'], admin['role'],
            'developer_mode_toggle',
            f'ativo={state}',
            ip=_get_client_ip(),
        )
    except Exception:
        pass
    return jsonify({'ok': True, 'enabled': state, 'visible': True})


@app.route('/api/auth/validate-key', methods=['POST'])
def api_validate_access_key():
    """Validate a SHA-256 access key and bind device_signature (one-time activation)."""
    data = request.get_json(silent=True) or {}
    access_key = (data.get('access_key') or '').strip()
    browser_hash = (data.get('browser_hash') or '').strip()
    browser_device_info = (data.get('browser_device_info') or '').strip() or None
    if len(access_key) != 64 or len(browser_hash) != 64:
        return jsonify({"ok": False, "error": "Parâmetros inválidos."}), 400
    result = admin_store.validate_access_key(access_key, browser_hash, browser_device_info)
    if not result['ok']:
        status = 403 if 'vinculada' in result.get('error', '') else 404
        return jsonify(result), status
    return jsonify(result), 200


@app.route('/api/admin/audit-supporter/<int:supporter_id>', methods=['GET'])
def api_admin_audit_supporter(supporter_id):
    """Owner/admin/moderator: return activation audit data for a supporter.
    Self-healing: legacy rows with NULL access_key/device_signature fields are
    handled gracefully with fallback display strings — never crashes on old data."""
    session_id = request.cookies.get('admin_session')
    admin = admin_store.get_session(session_id) if session_id else None
    admin_data = admin[0] if isinstance(admin, tuple) and len(admin) > 0 else admin
    if not admin_data or (isinstance(admin_data, dict) and admin_data.get('role') not in ('owner', 'admin', 'moderator')):
        return jsonify({"status": "error", "message": "Acesso negado"}), 403
    try:
        data = admin_store.audit_supporter(supporter_id)
        if data is None:
            return jsonify({"status": "error", "message": "Apoiador não localizado."}), 404
        # Format activated_at to Brazilian localized string: DD/MM/AAAA às HH:MM
        raw_ts = data.get("activated_at") or ""
        formatted_ts = "Pendente"
        if raw_ts and raw_ts != "—":
            try:
                from datetime import datetime as _dt
                # Handle both ISO strings and datetime objects
                if isinstance(raw_ts, str):
                    raw_ts_clean = raw_ts.split('.')[0].replace('T', ' ')
                    parsed = _dt.strptime(raw_ts_clean, "%Y-%m-%d %H:%M:%S")
                else:
                    parsed = raw_ts
                formatted_ts = parsed.strftime("%d/%m/%Y") + " às " + parsed.strftime("%H:%M")
            except Exception:
                formatted_ts = str(raw_ts)
        return jsonify({
            "status":              "success",
            "nickname":            data.get("nickname") or "Desconhecido",
            "is_activated":        bool(data.get("is_activated")) if data.get("is_activated") is not None else False,
            "device_signature":    data.get("device_signature") or "Pendente — usuário legado sem vínculo",
            "activated_at":        formatted_ts,
            "browser_device_info": data.get("browser_device_info") or "Usuário Legado / Não Identificado",
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro interno: {str(e)}"}), 500


@app.route('/api/admin/regenerate-key/<int:supporter_id>', methods=['POST'])
def api_admin_regenerate_key(supporter_id):
    """Owner only: wipe device binding and issue a fresh SHA-256 access key.
    Self-healing: safely patches legacy rows that were missing the new columns."""
    session_id = request.cookies.get('admin_session')
    admin = admin_store.get_session(session_id) if session_id else None
    admin_data = admin[0] if isinstance(admin, tuple) and len(admin) > 0 else admin
    if not admin_data or (isinstance(admin_data, dict) and admin_data.get('role') != 'owner'):
        return jsonify({"status": "error", "message": "Acesso negado"}), 403
    try:
        new_key = admin_store.regenerate_supporter_key(supporter_id)
        if new_key is None:
            return jsonify({"status": "error", "message": "Apoiador não encontrado."}), 404
        try:
            admin_store.audit(admin_data['username'] if isinstance(admin_data, dict) else admin_data.get('username', 'owner'), 'regenerate_access_key', f'supporter_id={supporter_id}')
        except Exception:
            pass  # audit failure must never block key delivery
        return jsonify({
            "status": "success",
            "message": "Nova chave SHA-256 gerada e perfil legado atualizado com sucesso!",
            "new_key": new_key,
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro no servidor: {str(e)}"}), 500


@app.route('/ping')
def ping():
    respostas = [
        "Pong! Tô aqui, firme e forte!",
        "Ping recebido! Tudo nos trinques!",
        "Hey! Servidor acordado e dançando!",
        "Pong! Vamos que vamos!",
        "Pong chegou! Servidor turbo ativado!"
    ]
    mensagem = random.choice(respostas)
    resp = jsonify({'message': mensagem})
    # Evita que proxies/CDNs (ex.: Hugging Face Spaces) façam cache da resposta
    # e "travem" o contador de próximo ping no dashboard.
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/scrape', methods=['POST'])
def scrape_media():
    """Endpoint para fazer scraping de mídia de uma URL"""
    try:
        url = request.form.get('url', '').strip()

        if not url:
            flash('Por favor, insira uma URL válida.', 'error')
            return render_template('index.html')

        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            flash('URL inválida. Certifique-se de incluir http:// ou https://', 'error')
            return render_template('index.html')

        try:
            response = requests.get(url, timeout=10, allow_redirects=True)

            # Inspecionar conteúdo mesmo com códigos como 410
            soup = bs4.BeautifulSoup(response.text, 'html.parser')
            content_text = soup.get_text(strip=True)

            # Triagem inteligente: termos de exclusão/erro embutidos no HTML (mesmo com status 200)
            _exclusion_terms = [
                "Álbum excluído por problemas de direitos autorais",
                "Album deleted for copyright issue",
                "Page not found",
                "Página não encontrada",
                "This content is no longer available",
                "Este conteúdo não está mais disponível",
                "404 not found",
                "Conteúdo removido",
                "Content removed",
            ]
            _lower_text = content_text.lower()
            _matched_term = next((t for t in _exclusion_terms if t.lower() in _lower_text), None)
            if _matched_term:
                flash(f"Não foi possível processar esta página: conteúdo indisponível ou removido ({_matched_term}).", 'warning')
                return render_template('index.html')
            elif response.status_code == 404:
                flash("Página não encontrada (Erro 404).", 'warning')
                return render_template('index.html')
            elif response.status_code == 410:
                flash("Esta página foi removida permanentemente pelo servidor.", 'warning')
                return render_template('index.html')
            elif response.status_code == 403:
                flash("Acesso proibido à página (Erro 403).", 'warning')
                return render_template('index.html')
            elif response.status_code == 401:
                flash("Autenticação necessária para acessar esta página (Erro 401).", 'warning')
                return render_template('index.html')
            elif response.status_code >= 500:
                flash("Erro no servidor do site. Tente novamente mais tarde.", 'warning')
                return render_template('index.html')

        except requests.exceptions.ConnectionError:
            flash("Erro de conexão. Verifique sua internet ou se o site está fora do ar.", 'error')
            return render_template('index.html')
        except requests.exceptions.Timeout:
            flash("O site está muito lento ou travado. Tente novamente.", 'error')
            return render_template('index.html')
        except requests.exceptions.RequestException:
            flash("Erro ao tentar acessar a página.", 'error')
            return render_template('index.html')

        # Fazer scraping real após verificar possíveis erros
        app_logger.info(f"🔍 Iniciando scraping da URL: {url}")
        media_data = scraper.scrape_media(url)

        if not media_data['images'] and not media_data['videos']:
            app_logger.warning("❌ Nenhuma mídia encontrada na página")
            flash('Nenhuma mídia encontrada nesta página.', 'warning')
            return render_template('index.html')

        app_logger.info(f"✅ Scraping concluído! 🖼️ Imagens: {len(media_data['images'])}, 🎬 Vídeos: {len(media_data['videos'])}")
        stats_store.increment('searches')
        stats_store.increment('images', len(media_data['images']))
        stats_store.increment('videos', len(media_data['videos']))
        stats_store.set_val('last_scrape', time.time())

        return render_template('results.html',
                               media_data=media_data,
                               original_url=url)

    except Exception as e:
        app_logger.error(f"💥 Erro inesperado durante o scraping: {str(e)}")
        flash("Ocorreu um erro inesperado. Tente novamente mais tarde.", 'error')
        return render_template('index.html')

@app.route('/api/check-media')
def api_check_media():
    """
    Server-side HEAD request para classificar o tipo de erro de uma URL de mídia.
    Retorna: {status: int, error_type: 'ok'|'404'|'403'|'copyright'|'error'}
    Evita problemas de CORS ao fazer a requisição no servidor.
    """
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'status': 0, 'error_type': 'error'}), 400
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return jsonify({'status': 0, 'error_type': 'error'}), 400

    # Palavras-chave de copyright na URL
    _copyright_kw = ('copyright', 'dmca', 'removed', 'deleted', 'excluido')
    if any(kw in url.lower() for kw in _copyright_kw):
        return jsonify({'status': 0, 'error_type': 'copyright'})

    try:
        parsed_u = urlparse(url)
        referer  = f"{parsed_u.scheme}://{parsed_u.netloc}/"
        hdrs = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer':    referer,
            'Origin':     referer.rstrip('/'),
        }
        r = requests.head(url, headers=hdrs, timeout=6, allow_redirects=True)
        code = r.status_code
        if code in (200, 206):
            return jsonify({'status': code, 'error_type': 'ok'})
        elif code in (404, 410):
            return jsonify({'status': code, 'error_type': '404'})
        elif code in (401, 403):
            return jsonify({'status': code, 'error_type': '403'})
        else:
            return jsonify({'status': code, 'error_type': 'error'})
    except requests.exceptions.Timeout:
        return jsonify({'status': 0, 'error_type': 'error'})
    except requests.exceptions.ConnectionError:
        return jsonify({'status': 0, 'error_type': 'error'})
    except Exception:
        return jsonify({'status': 0, 'error_type': 'error'})


@app.route('/validate_url', methods=['POST'])
def validate_url():
    """Endpoint AJAX para validação de URL em tempo real"""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'valid': False, 'message': 'URL não pode estar vazia'})
        
        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            return jsonify({'valid': False, 'message': 'Formato de URL inválido'})
        
        return jsonify({'valid': True, 'message': 'URL válida'})
    
    except Exception as e:
        return jsonify({'valid': False, 'message': 'Erro na validação'})

@app.route('/debug-info')
def debug_info_public():
    """Página informativa sobre o painel de debug."""
    return render_template('debug_info.html')

@app.route('/debug/login', methods=['GET', 'POST'])
def debug_login():
    return redirect('/admin/login')

@app.route('/debug/logout')
def debug_logout():
    return redirect('/admin/logout')

@app.route('/batch/panel')
def batch_panel_view():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'batch_config'):
        abort(404)
    batch_msg = session.pop('_batch_msg', None)
    internal_access_mod    = admin_store.get_internal_access_moderator()
    internal_access_helper = admin_store.get_internal_access_helper()
    all_active  = admin_store.list_admins()
    chat_admins = [{'id': a['id'], 'username': a['username'], 'display_name': a['display_name'],
                    'role': a['role'], 'avatar': a['avatar']}
                   for a in all_active if a['active'] and a['id'] != admin['id']]
    return render_template('batch_panel.html', admin=admin,
                           role_meta=admin_store.ROLE_META,
                           has_perm=admin_store.has_perm,
                           batch_msg=batch_msg,
                           internal_access_mod=internal_access_mod,
                           internal_access_helper=internal_access_helper,
                           chat_admins=chat_admins)


@app.route('/debug')
def debug_view():
    """Painel de debug — apenas URLs com falha, sem dados de clientes."""
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'debug_resolve'):
        abort(404)
    debug_msg = session.pop('_debug_msg', None)
    summary = debug_logger.get_summary()
    return render_template('debug.html', summary=summary, debug_msg=debug_msg,
                           admin=admin, role_meta=admin_store.ROLE_META,
                           has_perm=admin_store.has_perm)


@app.route('/debug/resolve', methods=['POST'])
def debug_resolve():
    admin = g.admin
    url = request.form.get('url', '').strip()
    if url:
        debug_logger.mark_resolved(url)
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               'URL resolvida', url[:200], _get_client_ip())
    session['_debug_msg'] = 'URL marcada como resolvida.'
    return redirect('/debug')


@app.route('/debug/unresolve', methods=['POST'])
def debug_unresolve():
    admin = g.admin
    url = request.form.get('url', '').strip()
    if url:
        debug_logger.unmark_resolved(url)
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               'URL desresolvida', url[:200], _get_client_ip())
    session['_debug_msg'] = 'URL movida de volta para não resolvida.'
    return redirect('/debug')


@app.route('/debug/delete_url', methods=['POST'])
def debug_delete_url():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'debug'):
        abort(404)
    url = request.form.get('url', '').strip()
    if url:
        debug_logger.delete_url(url)
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               'URL excluída do log', url[:200], _get_client_ip())
    session['_debug_msg'] = 'URL removida permanentemente do log.'
    return redirect('/debug')


@app.route('/debug/delete-domain', methods=['POST'])
def debug_delete_domain():
    """Exclusão em massa AJAX: remove todas as URLs de um domínio do log de debug."""
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'debug'):
        return jsonify({'success': False, 'error': 'Permissão negada.'}), 403
    data = request.get_json(silent=True) or {}
    domain = data.get('domain', '').strip()
    reason = data.get('reason', '').strip()
    if not domain:
        return jsonify({'success': False, 'error': 'Domínio não informado.'}), 400
    removed = debug_logger.delete_domain(domain)
    detail = f'{domain} ({removed} URLs)'
    if reason:
        detail += f' — Motivo: {reason}'
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Domínio excluído em massa do log', detail,
                           _get_client_ip())
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Domínio excluído do log de debug', detail, ip=_get_client_ip())
    return jsonify({'success': True, 'removed': removed, 'domain': domain})


@app.route('/debug/resolve_domain', methods=['POST'])
def debug_resolve_domain():
    admin = g.admin
    domain = request.form.get('domain', '').strip()
    changed = 0
    if domain:
        changed = debug_logger.mark_domain_resolved(domain)
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               'Domínio resolvido', domain, _get_client_ip())
    session['_debug_msg'] = f'{changed} URL(s) do domínio marcadas como resolvidas.'
    return redirect('/debug')


@app.route('/debug/clean', methods=['POST'])
def debug_clean():
    admin = g.admin
    # Destructive bulk action: only owner/admin; moderator/helper are blocked
    # even if they have the 'debug' view permission.
    if admin['role'] not in ('owner', 'admin'):
        abort(403)
    removed = debug_logger.delete_resolved()
    detail = f'{removed} URLs removidas'
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Log limpo (resolvidas)', detail, _get_client_ip())
    # Webhook — destructive actions must never be silent
    try:
        dwh.fire('admin_action', dwh.build_action_embed(
            admin['display_name'], admin['role'],
            'Log limpo (resolvidas)', detail, _get_client_ip()
        ))
    except Exception:
        pass
    session['_debug_msg'] = f'{removed} entrada(s) resolvida(s) removida(s) do log.'
    return redirect('/debug')


@app.route('/debug/clear', methods=['POST'])
def debug_clear():
    admin = g.admin
    # Destructive bulk action: only owner/admin; moderator/helper are blocked
    # even if they have the 'debug' view permission.
    if admin['role'] not in ('owner', 'admin'):
        abort(403)
    debug_logger.clear_all()
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Log apagado completamente', None, _get_client_ip())
    # Webhook — destructive actions must never be silent
    try:
        dwh.fire('admin_action', dwh.build_action_embed(
            admin['display_name'], admin['role'],
            'Log apagado completamente', 'Todos os registros de debug removidos', _get_client_ip()
        ))
    except Exception:
        pass
    session['_debug_msg'] = 'Log de debug apagado completamente.'
    return redirect('/debug')


@app.route('/debug/json')
def debug_json():
    summary = debug_logger.get_summary()
    return jsonify(summary)


@app.route('/dashboard')
def dashboard():
    _ensure_auto_ping()
    return render_template('dashboard.html')

def _get_app_version():
    """Detecta versão pelo número de commits git; usa __version__ como fallback."""
    try:
        import subprocess
        count = subprocess.check_output(
            ['git', 'rev-list', '--count', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        return f'v2.{count}'
    except Exception:
        return __version__

_APP_VERSION = _get_app_version()


def _get_memory_info():
    """Retorna uso de RAM do processo em MB e uma classificação."""
    try:
        import psutil
        proc = psutil.Process()
        rss_mb = proc.memory_info().rss / 1024 / 1024
        if rss_mb < 80:
            label = 'Excelente'
        elif rss_mb < 160:
            label = 'Boa'
        elif rss_mb < 300:
            label = 'Moderada'
        else:
            label = 'Alta'
        return round(rss_mb, 1), label
    except Exception:
        return None, 'N/A'

def _get_performance_score():
    """Score de performance baseado em memória + uptime contínuo."""
    try:
        import psutil
        rss_mb = psutil.Process().memory_info().rss / 1024 / 1024
        uptime_h = (time.time() - stats_store.start_time) / 3600
        # Penaliza alto uso de memória
        if rss_mb < 80 and uptime_h >= 0:
            return 'Excelente'
        elif rss_mb < 160:
            return 'Boa'
        elif rss_mb < 300:
            return 'Moderada'
        else:
            return 'Limitada'
    except Exception:
        return 'N/A'

@app.route('/dashboard/api')
def dashboard_api():
    _ensure_auto_ping()
    _ensure_next_ping_timestamp()
    real = stats_store.get_real()
    now = time.time()

    def display(key, default=0):
        return stats_store.get_display(key, default)

    uptime_seconds = int(now - stats_store.start_time)
    mem_mb, mem_label = _get_memory_info()
    perf = _get_performance_score()

    resp = jsonify({
        'uptime': uptime_seconds,
        'server_ts': now,                           # timestamp do servidor para sincronizar JS
        'views': display('views'),
        'searches': display('searches'),
        'images': display('images'),
        'videos': display('videos'),
        'downloads': display('downloads'),
        'last_scrape_ts': real.get('last_scrape'),  # timestamp bruto — JS formata em tempo real
        'next_ping_ts': real.get('next_ping'),      # timestamp bruto — JS faz countdown em tempo real
        'system': {
            'versao': _get_effective_version(),
            'status': 'Seguro',
            'memoria': f'{mem_mb} MB' if mem_mb is not None else 'N/A',
            'memoria_label': mem_label,
            'performance': perf,
            'mem_mb': mem_mb,
        },
    })
    # Mesma proteção do /ping: sem isso, proxies como o do Hugging Face Spaces
    # podem cachear a resposta e o cronômetro de "Próximo Ping" trava em "—".
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

def _get_effective_version() -> str:
    """Retorna versão customizada do DB se existir, senão versão detectada automaticamente."""
    override = admin_store.get_version_override()
    return override if override else _APP_VERSION


@app.route('/dashboard/config', methods=['GET', 'POST'])
def dashboard_config():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'debug'):
        abort(404)
    if request.method == 'POST':
        form_type = request.form.get('_form_type', 'stats')
        if form_type == 'version':
            # Apenas owner pode alterar a versão
            if admin['role'] != 'owner':
                abort(403)
            new_ver = request.form.get('version_override', '').strip()
            admin_store.set_version_override(new_ver if new_ver else None)
            session['_debug_msg'] = f'Versão atualizada para "{new_ver}".' if new_ver else 'Versão resetada para automática.'
        else:
            overrides = {}
            for key in ('views', 'searches', 'images', 'videos', 'downloads'):
                val = request.form.get(key, '').strip()
                if val:
                    try:
                        overrides[key] = int(val)
                    except ValueError:
                        pass
            stats_store.save_overrides(overrides)
            session['_debug_msg'] = 'Estatísticas do dashboard salvas.'
        return redirect('/dashboard/config')
    overrides = stats_store.get_overrides()
    real = stats_store.get_real()
    cfg_msg = session.pop('_debug_msg', None)
    mem_mb, mem_label = _get_memory_info()
    internal_access_mod    = admin_store.get_internal_access_moderator()
    internal_access_helper = admin_store.get_internal_access_helper()
    all_active  = admin_store.list_admins()
    chat_admins = [{'id': a['id'], 'username': a['username'], 'display_name': a['display_name'],
                    'role': a['role'], 'avatar': a['avatar']}
                   for a in all_active if a['active'] and a['id'] != admin['id']]
    version_override = admin_store.get_version_override()
    return render_template('dashboard_config.html',
                           admin=admin,
                           overrides=overrides, real=real, cfg_msg=cfg_msg,
                           app_version=_get_effective_version(),
                           app_version_auto=_APP_VERSION,
                           version_override=version_override,
                           sys_status='Seguro',
                           sys_memoria=(f'{mem_mb} MB' if mem_mb is not None else 'N/A'),
                           sys_performance=_get_performance_score(),
                           internal_access_mod=internal_access_mod,
                           internal_access_helper=internal_access_helper,
                           chat_admins=chat_admins)

@app.route('/dashboard/config/reset')
def dashboard_config_reset():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'debug'):
        abort(404)
    stats_store.save_overrides({})
    session['_debug_msg'] = 'Overrides removidos. Exibindo valores reais.'
    return redirect('/dashboard/config')

# ── Anti-tampering: nonces server-side ────────────────────────────────────
_payment_intents = {}  # {nonce: {amount, ts}}

def _new_intent(amount: float) -> str:
    nonce = uuid.uuid4().hex
    _payment_intents[nonce] = {'amount': amount, 'ts': time.time()}
    # Limpa intents expirados (> 30 min)
    cutoff = time.time() - 1800
    for k in list(_payment_intents):
        if _payment_intents[k]['ts'] < cutoff:
            del _payment_intents[k]
    return nonce

def _consume_intent(nonce: str):
    """Retorna o amount se o nonce for válido e não expirado, senão None."""
    entry = _payment_intents.pop(nonce, None)
    if not entry:
        return None
    if time.time() - entry['ts'] > 1800:
        return None
    return entry['amount']


def _public_base_url() -> str:
    """Build gateway return URLs using the public reverse-proxy host."""
    forwarded_host = request.headers.get('X-Forwarded-Host', '').split(',')[0].strip()
    host = forwarded_host or request.host
    forwarded_proto = request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip()
    proto = forwarded_proto if forwarded_proto in ('http', 'https') else (
        'https' if request.is_secure else 'http'
    )
    return f'{proto}://{host}'.rstrip('/')


@app.route('/payment/gateways')
def payment_gateways_list():
    method = request.args.get('method', 'pix')
    gws = pgw.get_enabled_card_gateways() if method == 'card' else pgw.get_enabled_pix_gateways()
    # Garante que o gateway primário venha primeiro na lista
    gws.sort(key=lambda g: (0 if g.get('primary') else 1))
    return jsonify(gws)

@app.route('/payment/intent', methods=['POST'])
def payment_intent_create():
    """Valida o valor server-side e retorna um nonce. O cliente nunca envia o valor para /payment/create."""
    try:
        data = request.get_json(force=True) or {}
        raw = data.get('amount')
        try:
            amount = float(str(raw).replace(',', '.'))
        except (ValueError, TypeError):
            return jsonify({'error': 'Valor inválido'}), 400
        if not math.isfinite(amount) or amount <= 0:
            return jsonify({'error': 'Valor inválido.'}), 400
        if amount < 1.0:
            return jsonify({'error': 'Valor mínimo para doação é R$ 1,00.'}), 400
        if amount > 999999.99:
            return jsonify({'error': 'Valor inválido. Tente novamente.'}), 400
        amount = round(amount, 2)
        nonce = _new_intent(amount)
        return jsonify({'nonce': nonce, 'amount': amount})
    except Exception:
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/payment/create', methods=['POST'])
def payment_create():
    """Cria o pagamento usando o nonce. O valor vem do servidor — impossível de falsificar."""
    try:
        data = request.get_json(force=True) or {}
        nonce = str(data.get('nonce', '')).strip()
        gateway_id = str(data.get('gateway', '')).strip()
        # 'pix' (padrão) ou 'card' — escolha do usuário na aba de checkout
        method = str(data.get('method', 'pix')).strip().lower()
        if method not in ('pix', 'card'):
            method = 'pix'

        # Recupera o valor do servidor (o cliente não envia o valor aqui)
        amount = _consume_intent(nonce)
        if amount is None:
            return jsonify({'error': 'Token expirado ou inválido. Gere um novo pagamento.'}), 400

        # Carrega configuração e resolve o gateway a usar
        cfg = pgw.load_config()
        primary = cfg.get('primary_gateway', 'manual')
        method_gateways = pgw.get_enabled_card_gateways() if method == 'card' else pgw.get_enabled_pix_gateways()

        # Se o cliente não especificou gateway (ou mandou 'manual' mas manual está desabilitado),
        # usa o primeiro gateway disponível para o método escolhido — sem trava rígida no 'manual'.
        if not gateway_id:
            gateway_id = next((g['id'] for g in method_gateways), primary)
        elif gateway_id == 'manual' and method == 'pix':
            manual_enabled = cfg.get('gateways', {}).get('manual', {}).get('enabled', True)
            if not manual_enabled:
                gateway_id = next((g['id'] for g in method_gateways), primary)

        # Verifica se o gateway escolhido está habilitado E disponível para o método pedido
        is_enabled = gateway_id in {g['id'] for g in method_gateways}
        if not is_enabled:
            # Último recurso: tenta qualquer gateway ativo para este método (evita falha total)
            fallback = next((g['id'] for g in method_gateways), None)
            if fallback:
                gateway_id = fallback
            else:
                return jsonify({'error': 'Nenhum meio de pagamento ativo para este método. Verifique as configurações.'}), 400

        gw = pgw.get_gateway(gateway_id)
        if not gw:
            return jsonify({'error': 'Gateway não encontrado'}), 400

        # The client disables the button for missing Stripe credentials, but
        # this server-side guard is authoritative and also covers forged
        # requests or a credential removed after the modal was opened.
        if gateway_id in ('stripe', 'mercadopago'):
            credential_status = pgw.gateway_credential_status(gateway_id, cfg)
            if not credential_status['ready']:
                return jsonify({'success': False, 'error': credential_status['error']}), 400

        # Item 3: em fluxos de redirect (cartão), o navegador sai do site e pode
        # não voltar à mesma aba — então o apelido/avatar são coletados AQUI,
        # antes de gerar o pagamento, e ficam salvos junto ao registro pendente.
        # Quando o gateway confirmar o pagamento (webhook), fulfill_pix_payment()
        # usa esses dados para completar o cadastro no ranking automaticamente.
        pending_nickname  = str(data.get('nickname', '')).strip()[:40] or None
        pending_avatar_id = str(data.get('avatar_id', '')).strip().lower() or None
        pending_style     = _clean_donor_style(data)
        pending_font_id   = pending_style['font_id']
        donor_token        = request.cookies.get('donation_token', '').strip() or None
        if pending_avatar_id and pending_avatar_id not in donor_avatars.get_valid_avatar_ids():
            pending_avatar_id = None

        if method == 'card' and gateway_id == 'stripe':
            base_url = _public_base_url()
            result = gw.create_card_payment(
                amount, 'Doação',
                success_url=f'{base_url}/payment/stripe/return?session_id={{CHECKOUT_SESSION_ID}}',
                cancel_url=f'{base_url}/?payment=cancel',
            )
        elif method == 'card' and gateway_id == 'paypal':
            base_url = _public_base_url()
            result = gw.create_pix_payment(
                amount, 'Doação',
                return_url=f'{base_url}/payment/paypal/capture',
                cancel_url=f'{base_url}/?payment=cancel',
            )
        else:
            result = gw.create_pix_payment(amount, 'Doação')

        # ── 401 Unauthorized: gateway não verificado / não configurado ────────
        # Bloqueia qualquer entrada no DB ou notificações; dispara alerta webhook
        if getattr(result, 'is_auth_error', False):
            try:
                raw_err_str = str(getattr(result, 'raw_api_error', {}))[:400]
                dwh.fire('payment', dwh.build_payment_embed(
                    action='mp_401_nao_configurado',
                    description=(
                        f'⚠️ Usuário tentou gerar QR Code via Mercado Pago mas falhou '
                        f'com **401 Unauthorized** (credenciais de produção não verificadas).\n'
                        f'`Erro API: {raw_err_str}`'
                    ),
                    amount=amount,
                    gateway=gateway_id,
                ))
            except Exception:
                pass
            return jsonify({
                'success': False,
                'error': result.error or '⚠️ Erro ao gerar o Pix com o Mercado Pago. Verifique as credenciais ou tente mais tarde.',
            }), 400

        if result.success and not result.payment_id:
            app_logger.error(f'Gateway {gateway_id} retornou pagamento sem identificador rastreável')
            return jsonify({
                'success': False,
                'error': 'Não foi possível vincular este pagamento com segurança. Tente novamente.',
            }), 503

        # Save the payment before returning the QR to the browser.  The browser
        # keeps a local copy so it can restore an accidentally closed modal, but
        # the database is the source of truth for status, cancellation and
        # supporter benefits.  Returning an untracked QR would allow a real
        # payment to become impossible to confirm.
        if result.payment_id:
            try:
                expires_in = getattr(result, 'expires_in', 600) or 600
                expires_at = (datetime.now() + timedelta(seconds=max(60, int(expires_in)))).isoformat(timespec='seconds')
                admin_store.save_pix_payment(
                    result.payment_id, gateway_id, amount, expires_at=expires_at,
                    nickname=pending_nickname, avatar_id=pending_avatar_id,
                    donor_token=donor_token, font_id=pending_font_id,
                    text_effect=pending_style['text_effect'],
                    name_color=pending_style['name_color'],
                    currency=getattr(result, 'currency', 'BRL')
                )
            except Exception as exc:
                app_logger.error(f'Não foi possível registrar o pagamento {result.payment_id}: {exc}')
                return jsonify({
                    'success': False,
                    'error': 'Não foi possível preparar este pagamento com segurança. Tente novamente.',
                }), 503
        # Dispara webhook de pagamento gerado
        try:
            _nick_ctx = pending_nickname or '—'
            dwh.fire('payment', dwh.build_payment_embed(
                action='pix_gerado',
                description=f'Novo QR Code PIX gerado para **{_nick_ctx}** — aguardando confirmação de pagamento.',
                amount=amount,
                gateway=gateway_id,
                payment_id=str(result.payment_id) if result.payment_id else None,
            ))
        except Exception:
            pass
        # Notificação financeira — visível para Owner e para membros com a
        # permissão individual de gerenciamento de gateways.
        try:
            val_fmt = f'R$ {amount:.2f}'.replace('.', ',')
            admin_store.add_notification(
                'payment',
                f'💰 Novo pagamento PIX gerado — {val_fmt} via gateway "{gateway_id}"',
                min_role='owner'
            )
        except Exception:
            pass
        return jsonify(result.to_dict())
    except Exception as e:
        app_logger.error(f'Erro em /payment/create: {e}')
        return jsonify({'error': 'Erro ao processar pagamento'}), 500

@app.route('/payment/status/<payment_id>')
def payment_status(payment_id):
    gateway_id = request.args.get('gateway', 'manual')
    gw = pgw.get_gateway(gateway_id)
    if not gw:
        return jsonify({'status': 'error'}), 404
    return jsonify(gw.check_payment_status(payment_id))


@app.route('/api/payment/status/<payment_id>')
def api_payment_status(payment_id):
    """
    Public status endpoint — read-only for clients.
    Auto-expires pending payments past their expires_at.
    Never accepts status changes from the client side.
    """
    if not payment_id or len(payment_id) > 200:
        return jsonify({'error': 'invalid'}), 400
    try:
        status = admin_store.auto_expire_pix_payment(payment_id)
    except Exception as e:
        app_logger.error(f'api_payment_status error: {e}')
        return jsonify({'status': 'error'}), 500
    if status == 'not_found':
        return jsonify({'status': 'not_found'}), 404
    # Safe recovery metadata only: never return donor tokens or gateway
    # credentials. The browser uses this to restore the exact checkout view
    # after an accidental tab/modal close.
    record = admin_store.get_pix_payment_by_id(payment_id)
    response = {'status': status}
    if record:
        response['amount'] = float(record.get('amount', 0) or 0)
        response['currency'] = record.get('currency', 'BRL')
        response['expires_at'] = str(record.get('expires_at', '') or '')
        if response['expires_at']:
            try:
                from datetime import datetime as _dt
                remaining = int((_dt.fromisoformat(response['expires_at']) - _dt.now()).total_seconds())
                response['remaining_seconds'] = max(0, remaining)
            except Exception:
                pass
    return jsonify(response)


@app.route('/api/payment/cancel/<payment_id>', methods=['POST'])
def api_payment_cancel(payment_id):
    """Cancel a pending PIX order only after the supporter changes its value."""
    if not payment_id or len(payment_id) > 200 or not re.fullmatch(r'[A-Za-z0-9_-]+', payment_id):
        return jsonify({'ok': False, 'status': 'invalid'}), 400
    try:
        status = admin_store.cancel_pix_payment(payment_id, 'supporter-back')
    except Exception as exc:
        app_logger.error(f'api_payment_cancel error: {exc}')
        return jsonify({'ok': False, 'status': 'error'}), 500
    return jsonify({
        'ok': status in ('cancelled', 'expired'),
        'status': status,
    })

def _fire_repeat_donation_alert(nickname: str, amount=None, gateway: str = None,
                                payment_id: str = None, access_key: str = None):
    """Dispatches the definitive repeat-donation motivational string, identically,
    to both the Discord webhook and the admin notification log, whenever an
    already-known supporter (existing ranking row) completes another donation.

    Args:
        nickname:   Supporter display name.
        amount:     Donation amount (float) for the embed value field.
        gateway:    Gateway used (e.g. 'Stripe', 'manual').
        payment_id: Immutable payment reference for admin cross-examination.
        access_key: SHA-256 supporter token for direct copy from Discord logs.
    """
    msg = (f'🔥 O apoiador de elite **{nickname}** acabou de fazer uma nova doação '
           f'e elevou seu prestígio no Ranking! Muito obrigado pelo apoio contínuo! 🙏')
    try:
        dwh.fire('payment', dwh.build_payment_embed(
            action='doacao_recorrente',
            description=msg,
            amount=amount,
            gateway=gateway,
            payment_id=payment_id,
            access_key=access_key,
        ))
    except Exception:
        pass
    try:
        admin_store.add_notification('payment', msg, min_role='owner')
    except Exception:
        pass


def _fulfill_and_broadcast(payment_id: str, updated_by: str):
    """Item 3 — handler global de fulfillment. Chama admin_store.fulfill_pix_payment()
    (marca 'paid' + completa o cadastro no ranking quando há nickname pré-coletado)
    e, se um cadastro foi de fato criado/atualizado aqui, dispara o mesmo broadcast
    de ticker + notificação que /api/donation/register já faz — mantendo o
    comportamento idêntico independente do gateway que confirmou o pagamento."""
    try:
        result = admin_store.fulfill_pix_payment(payment_id, updated_by)
    except Exception as exc:
        app_logger.error(f'_fulfill_and_broadcast error ({payment_id}): {exc}')
        return None
    if not result:
        return None
    try:
        import json as _json
        stats_store.set_val('latest_ranking_event', _json.dumps({
            'nickname':           result.get('nickname', ''),
            'avatar_id':          result.get('avatar_id') or '',
            'vip_level':          result.get('vip_level'),
            'discriminator':      result.get('discriminator', ''),
            'is_new':             result.get('is_new', True),
            'previous_vip_level': result.get('previous_vip_level'),
            'amount':             result.get('amount'),
            'ts':                 time.time(),
        }))
        if result.get('is_new') is False:
            _fire_repeat_donation_alert(
                result.get('nickname', ''),
                gateway=updated_by,
                payment_id=payment_id,
            )
        else:
            _notif_msg = (
                f'💎 Doação confirmada automaticamente ({updated_by}) — '
                f'{result.get("nickname","")} entrou no ranking VIP'
            )
            admin_store.add_notification('payment', _notif_msg, min_role='owner')
            try:
                dwh.fire('payment', dwh.build_payment_embed(
                    action='novo_doador',
                    description=(
                        f'🎉 **{result.get("nickname","")}** acaba de entrar para o '
                        f'Ranking de Apoiadores! Bem-vindo! 🏆'
                    ),
                    gateway=updated_by,
                    payment_id=payment_id,
                ))
            except Exception:
                pass
    except Exception:
        pass
    return result


def _mp_verify_signature(request_obj) -> bool:
    """
    Verifica a assinatura HMAC-SHA256 do Mercado Pago (x-signature header).
    Retorna True se a assinatura for válida OU se MERCADOPAGO_WEBHOOK_SECRET
    não estiver configurado (modo permissivo — verificação implícita via API lookup).
    """
    import hmac as _hmac
    import hashlib as _hashlib
    secret = os.environ.get('MERCADOPAGO_WEBHOOK_SECRET', '').strip()
    if not secret:
        return True  # Sem secret configurado: aceita (verificação implícita via API)

    sig_header = request_obj.headers.get('x-signature', '')
    req_id     = request_obj.headers.get('x-request-id', '')
    if not sig_header:
        app_logger.warning('[MP Webhook] x-signature ausente mas MERCADOPAGO_WEBHOOK_SECRET está configurado — rejeitando.')
        return False

    # Parseia ts= e v1= do cabeçalho
    ts = v1 = ''
    for part in sig_header.split(','):
        part = part.strip()
        if part.startswith('ts='):
            ts = part[3:]
        elif part.startswith('v1='):
            v1 = part[3:]

    if not ts or not v1:
        return False

    # Extrai data.id do corpo para construir o manifest
    body = request_obj.get_json(silent=True) or {}
    data_id = str(body.get('data', {}).get('id', '') or
                  request_obj.args.get('data.id', '') or
                  request_obj.form.get('data.id', '')).strip()

    manifest = f'id:{data_id};request-id:{req_id};ts:{ts}'
    expected = _hmac.new(secret.encode(), manifest.encode(), _hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, v1)


def _mp_extract_payment_id(body: dict, req) -> str | None:
    """
    Extrai o payment_id numérico de notificações do Mercado Pago.
    Suporta:
      - Formato v2 JSON:   {"action":"payment.*","data":{"id":"12345"}}
      - Formato legado:    {"resource":"...payments/12345","topic":"payment"}
      - Query/form params: ?topic=payment&id=12345  (formato legado alternativo)
    """
    action = body.get('action', '')
    topic  = body.get('topic', '') or req.args.get('topic', '') or req.form.get('topic', '')

    raw_id = None
    if action.startswith('payment'):
        raw_id = str(body.get('data', {}).get('id', '')).strip()
    elif topic == 'payment':
        # Formato legado JSON: resource = URL completa
        resource = body.get('resource', '') or req.args.get('resource', '') or req.form.get('resource', '')
        if resource:
            # Remove query string e trailing slash, pega último segmento
            path = resource.split('?')[0].rstrip('/')
            raw_id = path.split('/')[-1].strip()
        else:
            # Fallback: id direto na query/form
            raw_id = str(req.args.get('id', '') or req.form.get('id', '')).strip()

    if raw_id and re.fullmatch(r'\d{1,20}', raw_id):
        return raw_id
    return None


@app.route('/api/payments/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Recebe eventos do Stripe (checkout.session.completed) e confirma o
    pagamento de cartão no banco local — espelha o webhook do Mercado Pago
    para que o fulfillment (Item 3) funcione da mesma forma para qualquer
    gateway. Verificação de assinatura via STRIPE_WEBHOOK_SECRET quando
    configurado (modo permissivo — igual ao webhook do MP — quando ausente)."""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '').strip()
    try:
        import stripe
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        else:
            event = json.loads(payload or b'{}')
        event_type = event.get('type') if isinstance(event, dict) else event['type']
        data_obj = (event.get('data', {}) if isinstance(event, dict) else event['data']).get('object', {})
        if event_type == 'checkout.session.completed':
            session_id = data_obj.get('id')
            if session_id:
                _fulfill_and_broadcast(session_id, 'stripe-webhook')
                app_logger.info(f'[Stripe Webhook] Checkout {session_id} concluído → "paid".')
        return '', 200
    except Exception as e:
        app_logger.error(f'[Stripe Webhook] Erro: {e}')
        return '', 200  # 200 para o Stripe não ficar retentando em falha interna


@app.route('/payment/stripe/return')
def payment_stripe_return():
    """Rota de retorno do Checkout Stripe. Recupera a session, verifica
    payment_status == 'paid' e dispara o fulfillment global (ranking + broadcast)
    — espelhando o mesmo padrão do PayPal capture."""
    session_id = request.args.get('session_id', '').strip()
    if not session_id:
        return redirect('/?payment=cancel')
    gw = pgw.get_gateway('stripe')
    if not gw:
        return redirect('/?payment=cancel')
    try:
        import stripe
        stripe.api_key = gw.api_key
        stripe_session = stripe.checkout.Session.retrieve(session_id)
        # SDK Stripe retorna um objeto StripeObject, não um dict puro —
        # usar getattr() em vez de .get() para compatibilidade com todas as versões.
        payment_status = getattr(stripe_session, 'payment_status', None) or ''
        if payment_status == 'paid':
            _fulfill_and_broadcast(session_id, 'stripe-return')
            app_logger.info(f'[Stripe Return] Checkout {session_id} confirmado como pago → fulfillment concluído.')
            return redirect(f'/?payment=success&payment_id={session_id}')
        else:
            app_logger.warning(f'[Stripe Return] Checkout {session_id} com status "{payment_status}" — não confirmado.')
            return redirect('/?payment=cancel')
    except Exception as e:
        app_logger.error(f'[Stripe Return] Erro ao recuperar sessão {session_id}: {e}')
        return redirect('/?payment=cancel')


@app.route('/payment/paypal/capture')
def payment_paypal_capture():
    """Rota de retorno do checkout PayPal (application_context.return_url).
    Captura a ordem aprovada e confirma o pagamento — completando o
    fulfillment global (Item 3) exatamente como os demais gateways."""
    order_id = request.args.get('token', '').strip()  # PayPal envia o order id em ?token=
    if not order_id:
        return redirect('/?payment=cancel')
    gw = pgw.get_gateway('paypal')
    if not gw:
        return redirect('/?payment=cancel')
    try:
        capture = gw.capture_order(order_id)
        status = capture.get('status', '')
        if status == 'COMPLETED':
            _fulfill_and_broadcast(order_id, 'paypal-return')
            return redirect(f'/?payment=success&payment_id={order_id}')
        return redirect('/?payment=cancel')
    except Exception as e:
        app_logger.error(f'[PayPal Capture] Erro: {e}')
        return redirect('/?payment=cancel')


@app.route('/api/payments/mercado-pago/webhook', methods=['POST'])
def mercadopago_webhook():
    """
    Recebe notificações automáticas do Mercado Pago e atualiza o status do
    pagamento no banco de dados local.
    Autenticação: HMAC-SHA256 via MERCADOPAGO_WEBHOOK_SECRET (quando configurado).
    """
    try:
        # 1. Verificar autenticidade
        if not _mp_verify_signature(request):
            app_logger.warning('[MP Webhook] Assinatura inválida — requisição rejeitada.')
            return '', 200  # Retorna 200 para o MP não ficar retentando com payload inválido

        # 2. Extrair payment_id
        body = request.get_json(silent=True) or {}
        mp_payment_id = _mp_extract_payment_id(body, request)
        if not mp_payment_id:
            return '', 200  # Notificação não relacionada a pagamentos — ignora

        # 3. Consultar status real na API do MP (verificação implícita)
        gw = pgw.get_gateway('mercadopago')
        if not gw:
            return '', 200

        status_info = gw.check_payment_status(mp_payment_id)
        mp_status   = status_info.get('status', '')

        if mp_status == 'approved':
            record = admin_store.get_pix_payment_by_id(mp_payment_id)
            if record and record.get('status') != 'paid':
                val_fmt = f'R$ {float(record.get("amount", 0)):.2f}'.replace('.', ',')
                admin_store.add_notification(
                    'payment',
                    f'✅ Pagamento PIX aprovado automaticamente — {val_fmt} (MP #{mp_payment_id})',
                    min_role='owner'
                )
                # Item 3: marca 'paid' + completa o cadastro no ranking automaticamente
                # quando o fluxo trouxe nickname/avatar pré-coletados (checkout de cartão).
                _fulfill_and_broadcast(mp_payment_id, 'mercadopago-webhook')
                app_logger.info(f'[MP Webhook] Pagamento {mp_payment_id} aprovado → "paid".')
        elif mp_status in ('cancelled', 'rejected', 'refunded', 'charged_back'):
            record = admin_store.get_pix_payment_by_id(mp_payment_id)
            if record and record.get('status') == 'pending':
                admin_store.update_pix_payment_status(mp_payment_id, 'expired', 'mercadopago-webhook')
                app_logger.info(f'[MP Webhook] Pagamento {mp_payment_id} → expirado (MP: {mp_status}).')

        return '', 200
    except Exception as e:
        app_logger.error(f'[MP Webhook] Erro: {e}')
        return '', 200  # Sempre 200 para o MP não retentar em falha interna


def _mask_gateway_secret(value) -> str:
    """Keep secret values out of non-owner HTML, audit records and webhooks."""
    value = str(value or '')
    if not value:
        return ''
    if value.startswith('sk_test_'):
        return 'sk_test_...****'
    return '****'


def _masked_gateway_config(cfg: dict, owner: bool) -> dict:
    """Return a render-safe copy of the gateway configuration."""
    safe = copy.deepcopy(cfg)
    if owner:
        return safe
    sensitive = {
        'manual': ('pix_key',),
        'mercadopago': ('access_token',),
        '99pay': ('client_id', 'client_secret'),
        'stripe': ('stripe_test_secret_key', 'api_key'),
        'paypal': ('client_id', 'client_secret'),
    }
    for gid, keys in sensitive.items():
        data = safe.get('gateways', {}).get(gid, {})
        for key in keys:
            if data.get(key):
                data[key] = _mask_gateway_secret(data[key])
    for gateway in safe.get('custom_gateways', []):
        if gateway.get('pix_key'):
            gateway['pix_key'] = _mask_gateway_secret(gateway['pix_key'])
    return safe


def _secret_form_value(form, name: str, current: str) -> str:
    """Preserve a masked value submitted by a non-owner, while allowing edits."""
    if name not in form:
        return current or ''
    candidate = form.get(name, '').strip()
    if '****' in candidate or candidate == 'sk_test_...':
        return current or ''
    return candidate


def _gateway_config_from_form(current: dict, form) -> dict:
    """Build a complete candidate config, preserving masked/hidden secrets."""
    cfg = copy.deepcopy(current)
    cfg['primary_gateway'] = form.get('primary_gateway', 'manual')
    enabled = set(form.getlist('enabled_gateways'))
    for gid in list(pgw._GATEWAY_CLASSES.keys()):
        data = cfg.setdefault('gateways', {}).setdefault(gid, {})
        data['enabled'] = gid in enabled
        if gid == 'manual':
            data['pix_key'] = _secret_form_value(form, 'manual_pix_key', data.get('pix_key'))
            data['pix_key_type'] = form.get('manual_pix_key_type', 'email')
            data['city'] = form.get('manual_city', 'Brasil')
            data['beneficiary_name'] = form.get('manual_beneficiary_name', 'Doação')
        elif gid == 'mercadopago':
            data['access_token'] = _secret_form_value(
                form, 'mercadopago_access_token', data.get('access_token'))
        elif gid == '99pay':
            data['client_id'] = _secret_form_value(form, '99pay_client_id', data.get('client_id'))
            data['client_secret'] = _secret_form_value(
                form, '99pay_client_secret', data.get('client_secret'))
        elif gid == 'stripe':
            data['stripe_test_enabled'] = 'stripe_test_enabled' in form
            data['stripe_pix_enabled'] = 'stripe_pix_enabled' in form
            existing_test = data.get('stripe_test_secret_key', '')
            if not existing_test and str(data.get('api_key', '')).startswith('sk_test_'):
                existing_test = data['api_key']
            data['stripe_test_secret_key'] = _secret_form_value(
                form, 'stripe_test_secret_key', existing_test)
            # Do not retain an old production key in the live configuration.
            data.pop('api_key', None)
        elif gid == 'paypal':
            data['client_id'] = _secret_form_value(form, 'paypal_client_id', data.get('client_id'))
            data['client_secret'] = _secret_form_value(
                form, 'paypal_client_secret', data.get('client_secret'))
            data['sandbox'] = 'paypal_sandbox' in form

    cg_ids = form.getlist('cg_id')
    cg_names = form.getlist('cg_name')
    cg_pix_keys = form.getlist('cg_pix_key')
    cg_pix_key_types = form.getlist('cg_pix_key_type')
    cg_beneficiaries = form.getlist('cg_beneficiary_name')
    cg_cities = form.getlist('cg_city')
    cg_colors = form.getlist('cg_color')
    cg_icons = form.getlist('cg_icon')
    cg_enabled_flags = form.getlist('cg_enabled')
    old_custom = {str(x.get('id')): x for x in current.get('custom_gateways', [])}
    custom_gateways = []
    for i, raw_id in enumerate(cg_ids):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        name = cg_names[i] if i < len(cg_names) else 'Personalizado'
        if raw_id.startswith('new_'):
            slug = ''.join(c for c in name.lower() if c.isalnum() or c == '_')[:16] or 'custom'
            final_id = f'custom_{slug}_{uuid.uuid4().hex[:6]}'
        else:
            final_id = raw_id
        old = old_custom.get(final_id, {})
        posted_key = cg_pix_keys[i] if i < len(cg_pix_keys) else ''
        pix_key = posted_key.strip()
        if '****' in pix_key:
            pix_key = old.get('pix_key', '')
        custom_gateways.append({
            'id': final_id,
            'name': name[:40],
            'pix_key': pix_key,
            'pix_key_type': cg_pix_key_types[i] if i < len(cg_pix_key_types) else 'email',
            'beneficiary_name': (cg_beneficiaries[i] if i < len(cg_beneficiaries) else 'Doação')[:25],
            'city': (cg_cities[i] if i < len(cg_cities) else 'Brasil')[:15],
            'color': cg_colors[i] if i < len(cg_colors) else '#32d296',
            'icon': cg_icons[i] if i < len(cg_icons) else 'fa-qrcode',
            'enabled': i < len(cg_enabled_flags) and cg_enabled_flags[i] == '1',
        })
    cfg['custom_gateways'] = custom_gateways
    return cfg


_GATEWAY_AUDIT_DEFAULTS = {
    # These are the fields that can actually be edited in payment_admin.html.
    # Keeping the comparison allowlisted prevents legacy/internal keys from
    # creating a false audit entry when the complete form is submitted.
    'manual': {
        'enabled': True, 'pix_key': '', 'pix_key_type': 'chave-aleatoria',
        'beneficiary_name': 'Doação', 'city': 'Brasil',
    },
    'mercadopago': {'enabled': False, 'access_token': ''},
    '99pay': {'enabled': False, 'client_id': '', 'client_secret': ''},
    'stripe': {
        'enabled': False, 'stripe_test_enabled': False,
        'stripe_test_secret_key': '', 'stripe_pix_enabled': True,
    },
    'paypal': {
        'enabled': False, 'client_id': '', 'client_secret': '', 'sandbox': True,
    },
}
_CUSTOM_AUDIT_DEFAULTS = {
    'name': 'Personalizado', 'pix_key': '', 'pix_key_type': 'email',
    'beneficiary_name': 'Doação', 'city': 'Brasil', 'color': '#32d296',
    'icon': 'fa-qrcode', 'enabled': False,
}


def _normalise_gateway_for_audit(gid: str, data: dict) -> dict:
    """Return only user-editable gateway values in a stable shape.

    The admin form posts every gateway at once. Older saved configurations can
    also contain aliases such as Stripe's old ``api_key`` field. Comparing the
    raw dictionaries therefore makes untouched gateways look edited. Defaults
    make missing legacy keys equivalent to their current form values.
    """
    defaults = _GATEWAY_AUDIT_DEFAULTS.get(gid, {})
    source = data or {}
    result = {}
    for key, default in defaults.items():
        if gid == 'stripe' and key == 'stripe_test_secret_key':
            # api_key was the former name for a test key. It is not an extra
            # user edit and must not create a phantom Stripe change.
            legacy_key = source.get('api_key', '')
            value = source.get(key) or (
                legacy_key if str(legacy_key).startswith('sk_test_') else default
            )
        else:
            value = source.get(key, default)
        result[key] = value
    return result


def _normalise_custom_for_audit(data: dict) -> dict:
    source = data or {}
    return {
        'id': str(source.get('id', '')),
        **{
            key: source.get(key, default)
            for key, default in _CUSTOM_AUDIT_DEFAULTS.items()
        },
    }


def _gateway_change_scope(before: dict, after: dict) -> str:
    parts = _gateway_change_parts(before, after)
    changed = [pgw.GATEWAY_META[gid]['name'] for gid in parts['gateway_ids']]
    if parts['custom']:
        changed.append('gateways personalizados')
    if parts['primary'] and not changed:
        changed.append('gateway padrão')
    if len(changed) == 1:
        return changed[0]
    return 'múltiplas alterações' if changed else 'nenhuma alteração'


def _gateway_change_parts(before: dict, after: dict) -> dict:
    """Return only the gateway sections that differ between two configs."""
    changed_gateway_ids = [
        gid for gid in pgw.GATEWAY_META
        if _normalise_gateway_for_audit(
            gid, before.get('gateways', {}).get(gid, {})
        ) != _normalise_gateway_for_audit(
            gid, after.get('gateways', {}).get(gid, {})
        )
    ]

    before_custom = {
        str(item.get('id')): _normalise_custom_for_audit(item)
        for item in before.get('custom_gateways', [])
        if item.get('id')
    }
    after_custom = {
        str(item.get('id')): _normalise_custom_for_audit(item)
        for item in after.get('custom_gateways', [])
        if item.get('id')
    }
    custom_changes = []
    for custom_id in dict.fromkeys([*before_custom.keys(), *after_custom.keys()]):
        old_item = before_custom.get(custom_id)
        new_item = after_custom.get(custom_id)
        if old_item != new_item:
            custom_changes.append({
                'id': custom_id,
                'before': copy.deepcopy(old_item),
                'after': copy.deepcopy(new_item),
            })

    return {
        'gateway_ids': changed_gateway_ids,
        'custom': custom_changes,
        'primary': before.get('primary_gateway') != after.get('primary_gateway'),
    }


def _gateway_change_snapshot(before: dict, after: dict, side: str) -> dict:
    """Build a masked audit snapshot containing changed sections only."""
    parts = _gateway_change_parts(before, after)
    source = before if side == 'before' else after
    snapshot = {'gateways': {
        gid: copy.deepcopy(source.get('gateways', {}).get(gid, {}))
        for gid in parts['gateway_ids']
    }}
    if parts['custom']:
        snapshot['custom_gateways'] = [
            copy.deepcopy(item.get(side))
            for item in parts['custom']
            if item.get(side) is not None
        ]
    if parts['primary']:
        snapshot['primary_gateway'] = source.get('primary_gateway')
    return snapshot


def _annotate_gateway_change_request(item: dict) -> dict:
    """Add display metadata without changing the stored request payload."""
    parts = _gateway_change_parts(item['before_cfg'], item['proposed_cfg'])
    item['changed_gateway_ids'] = parts['gateway_ids']
    item['changed_custom_gateways'] = parts['custom']
    item['primary_changed'] = parts['primary']
    return item


def _gateway_diff_detail(before: dict, after: dict, pending: bool) -> str:
    scope = _gateway_change_scope(before, after)
    status = 'Aguardando aprovação do Owner' if pending else 'Aplicada pelo Owner'
    before_safe = json.dumps(
        _masked_gateway_config(_gateway_change_snapshot(before, after, 'before'), False),
        ensure_ascii=False, sort_keys=True
    )
    after_safe = json.dumps(
        _masked_gateway_config(_gateway_change_snapshot(before, after, 'after'), False),
        ensure_ascii=False, sort_keys=True
    )
    return (
        '```diff\n'
        f'- Estado anterior ({scope}): {before_safe}\n'
        f'+ Valor proposto ({scope}): {after_safe}\n'
        f'``` {status}'
    )


def _save_or_queue_gateway_config(admin: dict, before: dict, proposed: dict) -> dict:
    """Owners publish immediately; other authorized users enter the queue."""
    scope = _gateway_change_scope(before, proposed)
    if scope == 'nenhuma alteração':
        return {'queued': False, 'changed': False, 'scope': scope}

    if admin.get('role') == 'owner':
        pgw.save_config(proposed)
        detail = _gateway_diff_detail(before, proposed, False)
        admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                          'Gateway salvo diretamente', detail,
                          json.dumps(_masked_gateway_config(
                              _gateway_change_snapshot(before, proposed, 'before'), False
                          ), ensure_ascii=False),
                          json.dumps(_masked_gateway_config(
                              _gateway_change_snapshot(before, proposed, 'after'), False
                          ), ensure_ascii=False),
                          _get_client_ip())
        return {'queued': False, 'changed': True, 'scope': scope}

    request_id = admin_store.create_gateway_change_request(
        admin['id'], admin['display_name'], admin['role'], scope, before, proposed)
    detail = _gateway_diff_detail(before, proposed, True)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      f'Alteração de gateway enviada #{request_id}', detail,
                      json.dumps(_masked_gateway_config(
                          _gateway_change_snapshot(before, proposed, 'before'), False
                      ), ensure_ascii=False),
                      json.dumps(_masked_gateway_config(
                          _gateway_change_snapshot(before, proposed, 'after'), False
                      ), ensure_ascii=False),
                      _get_client_ip())
    admin_store.add_notification(
        'payment',
        f'⏳ Nova solicitação de gateway de {admin["display_name"]}: {scope}',
        min_role='owner')
    return {'queued': True, 'changed': True, 'request_id': request_id, 'scope': scope}


@app.route('/payment/admin/disable-all', methods=['POST'])
def payment_admin_disable_all():
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    before = pgw.load_config()
    cfg = copy.deepcopy(before)
    # Save which were active before disabling
    prev_active = [gid for gid, gdata in cfg.get('gateways', {}).items() if gdata.get('enabled')]
    prev_active += [cg['id'] for cg in cfg.get('custom_gateways', []) if cg.get('enabled')]
    cfg['previously_active_ids'] = prev_active
    for gid in cfg.get('gateways', {}):
        cfg['gateways'][gid]['enabled'] = False
    for cg in cfg.get('custom_gateways', []):
        cg['enabled'] = False
    result = _save_or_queue_gateway_config(admin, before, cfg)
    return jsonify({'ok': True, 'previously_active': prev_active, **result})


@app.route('/payment/admin/reactivate-all', methods=['POST'])
def payment_admin_reactivate_all():
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    before = pgw.load_config()
    cfg = copy.deepcopy(before)
    prev = cfg.get('previously_active_ids', [])
    if not prev:
        return jsonify({'error': 'Nenhum gateway para reativar'}), 400
    for gid in prev:
        if gid in cfg.get('gateways', {}):
            cfg['gateways'][gid]['enabled'] = True
        else:
            for cg in cfg.get('custom_gateways', []):
                if cg['id'] == gid:
                    cg['enabled'] = True
    cfg['previously_active_ids'] = []
    result = _save_or_queue_gateway_config(admin, before, cfg)
    return jsonify({'ok': True, 'reactivated': prev, **result})


@app.route('/payment/admin/set-primary-gateway', methods=['POST'])
def payment_admin_set_primary_gateway():
    """AJAX: salva o gateway padrão imediatamente ao mudar o dropdown.
    Usa load_config_locked + save_config (com Lock interno) para evitar
    race condition com o form submit completo de /payment/admin."""
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    gid = data.get('gid', '').strip()
    if not gid:
        return jsonify({'error': 'gid obrigatório'}), 400
    # Lê config sob lock para não colidir com saves simultâneos
    before = pgw.load_config_locked()
    cfg = copy.deepcopy(before)
    # Verifica se o gid é um gateway válido (padrão ou personalizado)
    known = list(pgw._GATEWAY_CLASSES.keys()) + [cg['id'] for cg in cfg.get('custom_gateways', [])]
    if gid not in known:
        return jsonify({'error': 'Gateway desconhecido'}), 400
    cfg['primary_gateway'] = gid
    result = _save_or_queue_gateway_config(admin, before, cfg)
    return jsonify({'ok': True, 'primary_gateway': gid, **result})


@app.route('/payment/admin/toggle-gateway', methods=['POST'])
def payment_admin_toggle_gateway():
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    gid = data.get('gid', '').strip()
    raw_enabled = data.get('enabled')
    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    elif raw_enabled in (0, 1, '0', '1'):
        enabled = str(raw_enabled) == '1'
    else:
        return jsonify({'ok': False, 'error': 'Estado da permissão inválido.'}), 400
    if not gid:
        return jsonify({'error': 'gid obrigatório'}), 400
    before = pgw.load_config()
    cfg = copy.deepcopy(before)
    found = False
    if gid in cfg.get('gateways', {}):
        cfg['gateways'][gid]['enabled'] = enabled
        found = True
    else:
        for cg in cfg.get('custom_gateways', []):
            if cg['id'] == gid:
                cg['enabled'] = enabled
                found = True
    if not found:
        return jsonify({'error': 'Gateway não encontrado'}), 404
    # Clear previously_active_ids when manually toggling
    cfg['previously_active_ids'] = []
    result = _save_or_queue_gateway_config(admin, before, cfg)
    return jsonify({'ok': True, 'gid': gid, 'enabled': enabled, **result})


@app.route('/payment/admin/toggle-stripe-pix', methods=['POST'])
def payment_admin_toggle_stripe_pix():
    """AJAX: salva imediatamente o toggle 'Habilitar Pix via Stripe' (Item 8),
    igual ao padrão real-time já usado para ativar/desativar gateways."""
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled', False))
    before = pgw.load_config_locked()
    cfg = copy.deepcopy(before)
    cfg.setdefault('gateways', {}).setdefault('stripe', {})['stripe_pix_enabled'] = enabled
    result = _save_or_queue_gateway_config(admin, before, cfg)
    return jsonify({'ok': True, 'enabled': enabled, **result})


@app.route('/payment/admin/paypal-sandbox-toggle', methods=['POST'])
def payment_admin_paypal_sandbox_toggle():
    """AJAX: salva imediatamente o toggle 'Habilitar Sandbox PayPal' sem recarregar a página."""
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    sandbox = bool(data.get('sandbox', True))
    before = pgw.load_config_locked()
    cfg = copy.deepcopy(before)
    cfg.setdefault('gateways', {}).setdefault('paypal', {})['sandbox'] = sandbox
    result = _save_or_queue_gateway_config(admin, before, cfg)
    return jsonify({'ok': True, 'sandbox': sandbox, **result})


@app.route('/payment/disabled-status')
def payment_disabled_status():
    return jsonify({'disabled': pgw.are_payments_disabled()})


def _has_gateway_access(admin) -> bool:
    """Gateway access is controlled by the target user's permission flag."""
    return admin_store.has_admin_permission(admin, 'manageGateways')


@app.route('/payment/admin/pix-status', methods=['GET'])
def payment_admin_pix_status():
    admin, _ = _get_admin()
    if not admin or admin['role'] != 'owner':
        return jsonify({'error': 'Não autorizado'}), 403
    rows = admin_store.get_pix_payments(50)
    result = []
    for p in rows:
        # auto-expire pending rows whose time has passed
        if p['status'] == 'pending':
            p['status'] = admin_store.auto_expire_pix_payment(p['payment_id'])
        # Item 1: retorna todos os status (incluindo expired) — filtragem feita no cliente
        result.append({
            'payment_id': p['payment_id'],
            'gateway_id': p.get('gateway_id', ''),
            'amount':     float(p.get('amount', 0)),
            'status':     p['status'],
            'ts':         str(p.get('ts', ''))[:16].replace('T', ' '),
            'expires_at': str(p.get('expires_at', '') or ''),
        })
    return jsonify(result)


@app.route('/payment/admin/pix-delete', methods=['POST'])
def payment_admin_pix_delete():
    admin, _ = _get_admin()
    if not admin or admin['role'] != 'owner':
        return jsonify({'error': 'Não autorizado'}), 403
    # Aceita tanto JSON (exclusão assíncrona via fetch, com animação de "shredding"
    # no front-end) quanto form-encoded (compatibilidade com qualquer submit antigo).
    data = request.get_json(silent=True) or {}
    payment_id = (data.get('payment_id') or request.form.get('payment_id', '')).strip()
    if not payment_id:
        return jsonify({'error': 'payment_id obrigatório'}), 400
    admin_store.delete_pix_payment(payment_id)
    if request.is_json:
        return jsonify({'ok': True, 'payment_id': payment_id})
    session['_debug_msg'] = f'PIX {payment_id} removido.'
    return redirect('/payment/admin')


@app.route('/payment/admin/pix-update', methods=['POST'])
def payment_admin_pix_update():
    admin, _ = _get_admin()
    if not admin or admin['role'] != 'owner':
        return jsonify({'error': 'Não autorizado'}), 403
    payment_id = request.form.get('payment_id', '').strip()
    status     = request.form.get('status', 'pending').strip()
    if not payment_id:
        return jsonify({'error': 'payment_id obrigatório'}), 400
    if status in ('paid', 'approved'):
        # Item 3: aprovação manual do admin também passa pelo fulfillment global.
        _fulfill_and_broadcast(payment_id, admin['display_name'])
    else:
        admin_store.update_pix_payment_status(payment_id, status, admin['display_name'])
    session['_debug_msg'] = f'Status do PIX {payment_id} atualizado para {status}.'
    return redirect('/payment/admin')


@app.route('/payment/admin', methods=['GET', 'POST'])
def payment_admin():
    admin, _ = _get_admin()
    if not admin:
        return redirect('/admin/login')
    if not _has_gateway_access(admin):
        # Item 2: retorna tela "Acesso Negado" para não-owners sem permissão
        return render_template('admin/gateway_denied.html', admin=admin,
                               role_meta=admin_store.ROLE_META), 403
    if request.method == 'POST':
        before = pgw.load_config_locked()
        cfg = _gateway_config_from_form(before, request.form)
        test_key = cfg.get('gateways', {}).get('stripe', {}).get('stripe_test_secret_key', '')
        if test_key and not test_key.startswith('sk_test_'):
            session['_debug_msg'] = 'Erro: a STRIPE TEST SECRET KEY deve começar com sk_test_.'
            return redirect('/payment/admin')
        result = _save_or_queue_gateway_config(admin, before, cfg)
        if not result.get('changed', True):
            session['_debug_msg'] = 'Nenhuma alteração detectada. A solicitação não foi criada.'
            return redirect('/payment/admin')
        if result.get('queued'):
            session['_gateway_notice'] = (
                '⚠️ Alteração enviada com sucesso! Como esta é uma configuração crítica de pagamento, '
                'as novas credenciais foram retidas e estão aguardando a aprovação manual do Proprietário (Owner) '
                'para entrarem em vigor.'
            )
        session['_debug_msg'] = (
            'Configurações de pagamento salvas!'
            if not result.get('queued') else
            'Alteração enviada para aprovação do Owner.'
        )
        return redirect('/payment/admin')

    cfg = _masked_gateway_config(pgw.load_config(), admin.get('role') == 'owner')
    admin_msg = session.pop('_debug_msg', None)
    gateway_notice = session.pop('_gateway_notice', None)
    pix_payments        = admin_store.get_pix_payments(50) if admin['role'] == 'owner' else []
    gateway_backups     = admin_store.get_gateway_backups(10) if admin['role'] == 'owner' else []
    any_active = not pgw.are_payments_disabled()
    has_previously_active = bool(cfg.get('previously_active_ids'))
    return render_template('payment_admin.html', cfg=cfg,
                           meta=pgw.GATEWAY_META,
                           custom_icons=pgw.CUSTOM_ICONS,
                           custom_colors=pgw.CUSTOM_COLORS,
                           admin_msg=admin_msg,
                           gateway_notice=gateway_notice,
                           admin=admin,
                           pix_payments=pix_payments,
                           gateway_backups=gateway_backups,
                           any_active=any_active,
                           has_previously_active=has_previously_active,
                           gateway_requests=(
                               admin_store.get_pending_gateway_change_requests()
                               if admin.get('role') == 'owner' else []
                           ))


@app.route('/admin/gateway/requests')
def admin_gateway_requests():
    admin = g.admin
    if admin.get('role') != 'owner':
        abort(404)
    requests_pending = [
        _annotate_gateway_change_request(item)
        for item in admin_store.get_pending_gateway_change_requests()
    ]
    return render_template(
        'admin/gateway_requests.html',
        admin=admin,
        requests=requests_pending,
        gateway_meta=pgw.GATEWAY_META,
        masked_config=_masked_gateway_config,
    )


@app.route('/admin/gateway/requests/<int:request_id>/details')
def admin_gateway_request_details(request_id):
    """Owner-only reveal endpoint for the before/after gateway comparison."""
    admin = g.admin
    if admin.get('role') != 'owner':
        return jsonify({'ok': False, 'error': 'Apenas o Owner pode visualizar os dados completos.'}), 403
    change = admin_store.get_gateway_change_request(request_id)
    if not change:
        return jsonify({'ok': False, 'error': 'Solicitação não encontrada.'}), 404
    response = jsonify({
        'ok': True,
        'before': change['before_cfg'],
        'after': change['proposed_cfg'],
    })
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


@app.route('/admin/gateway/requests/<int:request_id>/decision', methods=['POST'])
def admin_gateway_request_decision(request_id):
    admin = g.admin
    if admin.get('role') != 'owner':
        return jsonify({'ok': False, 'error': 'Apenas o Owner pode decidir.'}), 403
    data = request.get_json(silent=True) or {}
    decision = str(data.get('decision', '')).strip().lower()
    change = admin_store.get_gateway_change_request(request_id)
    if not change or change.get('status') != 'pending':
        return jsonify({'ok': False, 'error': 'Solicitação já decidida ou não encontrada.'}), 404
    if decision == 'approved':
        try:
            pgw.save_config(change['proposed_cfg'])
        except Exception as exc:
            return jsonify({'ok': False, 'error': f'Não foi possível aplicar: {str(exc)[:120]}'}), 500
    elif decision != 'rejected':
        return jsonify({'ok': False, 'error': 'Decisão inválida.'}), 400
    decided = admin_store.decide_gateway_change_request(
        request_id, decision, admin['display_name'],
        'Aprovado e publicado' if decision == 'approved' else 'Rejeitado e descartado')
    if not decided:
        return jsonify({'ok': False, 'error': 'Solicitação já decidida.'}), 409
    scope = decided.get('scope', 'gateway')
    action = 'Alteração de gateway aprovada' if decision == 'approved' else 'Alteração de gateway rejeitada'
    detail = (
        f'{scope} — {"credenciais propostas publicadas" if decision == "approved" else "configuração anterior preservada"}'
    )
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      f'{action} #{request_id}', detail,
        json.dumps(_masked_gateway_config(
            _gateway_change_snapshot(
                decided['before_cfg'], decided['proposed_cfg'], 'before'
            ), False
        ), ensure_ascii=False),
        json.dumps(_masked_gateway_config(
            _gateway_change_snapshot(
                decided['before_cfg'], decided['proposed_cfg'], 'after'
            ), False
        ), ensure_ascii=False),
                      _get_client_ip())
    admin_store.add_notification(
        'payment',
        f'{"✅" if decision == "approved" else "❌"} Solicitação de gateway #{request_id} '
        f'{"aprovada e publicada" if decision == "approved" else "rejeitada"} por {admin["display_name"]}',
        min_role='owner')
    return jsonify({'ok': True, 'status': decision})


def _ping_loop():
    """Daemon thread: DB-driven keep-alive loop for Hugging Face / Replit.

    Reads `next_ping` timestamp from the database at startup.
    • If the stored timestamp is still in the future → sleeps precisely until
      that moment (checking every 5 s so it can react to manual changes).
    • When the time arrives → fires the HTTP ping → writes a fresh
      `next_ping` timestamp back to the DB → sleeps again.

    Because the target timestamp lives in the DB, server restarts resume
    from where they left off — the browser countdown never resets on F5.
    """
    configured_url = (
        os.environ.get('KEEPALIVE_URL')
        or os.environ.get('APP_URL')
        or os.environ.get('SPACE_URL')
        or os.environ.get('SPACE_HOST')
        or os.environ.get('REPLIT_DEV_DOMAIN')
        or os.environ.get('REPLIT_DOMAINS')
    )
    if configured_url:
        configured_url = configured_url.strip()
        if not configured_url.startswith(('http://', 'https://')):
            configured_url = 'https://' + configured_url
        url = configured_url.rstrip('/')
        if not url.endswith('/ping'):
            url += '/ping'
    else:
        # Local fallback keeps the loop useful in development. Deployments
        # should set KEEPALIVE_URL to their public Space/app URL.
        url = 'http://127.0.0.1:5000/ping'

    INTERVAL_MIN = 240   # minimum seconds between pings (4 min)
    INTERVAL_MAX = 300   # maximum seconds between pings (5 min)
    CHECK_EVERY  = 5     # polling granularity in seconds

    while True:
        try:
            now      = time.time()
            real     = stats_store.get_real()
            next_ts  = real.get('next_ping')

            # ── If a valid future timestamp exists, keep sleeping ──────────
            if next_ts and next_ts > now:
                # Sleep até, no máximo, CHECK_EVERY s — ou exatamente o
                # tempo restante se for menor, para não disparar cedo.
                time.sleep(min(CHECK_EVERY, next_ts - now))
                continue

            # ── Time to ping ──────────────────────────────────────────────
            wait = random.randint(INTERVAL_MIN, INTERVAL_MAX)
            # Write the next target BEFORE the request so the countdown
            # is visible on the dashboard immediately after the ping.
            stats_store.set_val('next_ping', time.time() + wait)

            start = time.time()
            try:
                response = requests.get(
                    url,
                    params={'_keepalive': int(time.time())},
                    headers={'User-Agent': 'MediaScraper-KeepAlive/1.0'},
                    timeout=15,
                )
                latency_ms = int((time.time() - start) * 1000)
                stats_store.set_val('last_ping_latency', latency_ms)
                # Successful keep-alives are expected and happen every few
                # minutes. Keep them available for troubleshooting without
                # spamming the Hugging Face/debug console at INFO level.
                app_logger.debug(
                    f"🏓 Auto-ping — Status: {response.status_code} ({latency_ms}ms)"
                )
            except Exception as exc:
                app_logger.error(f"❌ Ping falhou: {exc}")

            # Sleep a full interval before checking again
            time.sleep(CHECK_EVERY)

        except Exception as exc:
            app_logger.error(f"❌ Erro no loop de ping: {exc}")
            time.sleep(30)


def auto_ping():
    """Starts the DB-driven keep-alive daemon thread (called once at startup).

    Waits for _boot_load to finish reading the DB before deciding whether a
    fresh next_ping is needed — this prevents the race condition where
    auto_ping() writes a value and _boot_load later overwrites it with a
    stale DB snapshot.
    """
    # Wait up to 8 s for the async DB boot-load to complete so we read the
    # correct persisted next_ping before touching it.
    loaded = stats_store.wait_for_boot(timeout=8.0)
    if not loaded:
        app_logger.warning("⚠️  stats boot-load não terminou em 8 s — continuando sem ele")

    real     = stats_store.get_real()
    existing = real.get('next_ping')
    now      = time.time()
    if not existing or existing <= now:
        # No valid future timestamp in DB — set one so the dashboard can
        # display a countdown right away; _ping_loop does the real work.
        stats_store.set_val('next_ping', now + random.randint(300, 540))

    t = threading.Thread(target=_ping_loop, daemon=True, name='ping-loop')
    t.start()
    app_logger.info("🗓️  Thread de auto-ping (DB-driven) iniciada")


_auto_ping_start_lock = threading.Lock()
_auto_ping_started = False


def _auto_ping_bootstrap():
    """Run auto-ping initialization outside the first WSGI request."""
    global _auto_ping_started
    try:
        auto_ping()
    except Exception:
        app_logger.exception("❌ Falha ao iniciar o auto-ping")
        with _auto_ping_start_lock:
            _auto_ping_started = False


def _ensure_auto_ping():
    """Start the ping worker once per process, including under WSGI servers."""
    global _auto_ping_started
    if _auto_ping_started:
        return
    with _auto_ping_start_lock:
        if _auto_ping_started:
            return
        _auto_ping_started = True
        threading.Thread(
            target=_auto_ping_bootstrap,
            daemon=True,
            name='auto-ping-bootstrap',
        ).start()


def _ensure_next_ping_timestamp():
    """Seed a visible countdown while the async worker is bootstrapping."""
    real = stats_store.get_real()
    existing = real.get('next_ping')
    if existing and existing > time.time():
        return existing
    next_ping = time.time() + 420
    stats_store.set_val('next_ping', next_ping)
    return next_ping


def _pix_expiry_worker():
    """Background thread: bulk-expires pending PIX payments past their timeout (runs every 60s)."""
    while True:
        time.sleep(60)
        try:
            expired = admin_store.expire_all_pending_pix()
            if expired:
                app_logger.info(f"[PIX] {expired} pagamento(s) expirado(s) automaticamente.")
        except Exception as e:
            app_logger.error(f"[PIX] Erro na thread de expiração: {e}")

# ── Batch Test ─────────────────────────────────────────────────────────────

def _get_batch_config() -> dict:
    cfg = pgw.load_config()
    defaults = {'max_urls': 20, 'delay_ms': 1000}
    bc = cfg.get('batch_config', {})
    return {**defaults, **bc}


def _get_session_vip_level():
    """Retorna o vip_level (int 1-5) do cookie de doação atual, ou None se
    não houver doador vinculado / token inválido — usado para exibir o
    selo de bônus VIP aplicado ao lado do limite de URLs."""
    try:
        token = request.cookies.get('donation_token', '')
        if token:
            row = admin_store.get_vip_by_token(token)
            if row:
                return int(row['vip_level'])
    except Exception:
        pass
    return None


def _get_effective_max_urls() -> int:
    """Base max_urls boosted by VIP level, pulling tier bonuses from vip_tiers_config DB."""
    if _developer_admin():
        return math.inf
    base = _get_batch_config()['max_urls']
    try:
        vip = _get_session_vip_level()
        if vip is not None:
            tiers = admin_store.get_vip_tiers()
            tier_map = {int(t['tier_id']): t for t in tiers}
            tier = tier_map.get(vip)
            if tier:
                bonus = int(tier['bonus_urls'])
                return base + bonus
    except Exception:
        pass
    return base


@app.context_processor
def inject_max_urls():
    donor_nickname = None
    donor_vip_level = None
    donor_discriminator = None
    donor_total_donated = None
    donor_font_id = 'default'
    donor_text_effect = 'solid'
    donor_name_color = '#f3f4f6'
    try:
        token = request.cookies.get('donation_token', '')
        if token:
            row = admin_store.get_vip_by_token(token)
            if row:
                donor_nickname      = row.get('nickname')      or None
                donor_vip_level     = row.get('vip_level')     or None
                donor_discriminator = row.get('discriminator') or None
                donor_total_donated = float(row.get('total_donated') or 0.0)
                donor_font_id = row.get('font_id') or 'default'
                donor_text_effect = row.get('text_effect') or 'solid'
                donor_name_color = row.get('name_color') or '#f3f4f6'
    except Exception:
        pass
    developer_admin, developer_available, developer_enabled = _developer_ui_state()
    return {
        'max_urls': _get_effective_max_urls(),
        'max_urls_display': '∞' if developer_enabled else _get_effective_max_urls(),
        'session_vip_level': _get_session_vip_level(),
        'donor_nickname': donor_nickname,
        'donor_vip_level': donor_vip_level,
        'donor_discriminator': donor_discriminator,
        'donor_total_donated': donor_total_donated,
        'donor_font_id': donor_font_id,
        'donor_text_effect': donor_text_effect,
        'donor_name_color': donor_name_color,
        'developer_admin': developer_admin,
        'developer_available': developer_available,
        'developer_enabled': developer_enabled,
        'developer_role_label': (
            admin_store.ROLE_META.get(developer_admin.get('role'), {}).get('label')
            if developer_admin else None
        ),
    }


@app.route('/batch/config', methods=['GET'])
def batch_config_get():
    return jsonify(_get_batch_config())

@app.route('/batch/config', methods=['POST'])
def batch_config_save():
    admin, _ = _get_admin()
    if not admin or not admin_store.has_perm(admin['role'], 'batch_config'):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    max_urls = max(2, min(100, int(data.get('max_urls', 20))))
    delay_ms = max(0, min(10000, int(data.get('delay_ms', 1000))))
    cfg = pgw.load_config()
    cfg['batch_config'] = {'max_urls': max_urls, 'delay_ms': delay_ms}
    pgw.save_config(cfg)
    return jsonify({'ok': True, 'max_urls': max_urls, 'delay_ms': delay_ms})

@app.route('/batch/stream', methods=['POST'])
def batch_stream():
    data = request.get_json(silent=True) or {}
    raw_urls = data.get('urls', [])
    bc       = _get_batch_config()
    developer_admin = _developer_admin()
    max_urls = math.inf if developer_admin else _get_effective_max_urls()
    delay_ms = bc['delay_ms']

    if not isinstance(raw_urls, list):
        return jsonify({'error': 'Lista de URLs inválida.'}), 400
    urls = [str(u).strip() for u in raw_urls if str(u).strip()]
    if len(urls) < 2:
        return jsonify({'error': 'Mínimo de 2 URLs.'}), 400
    if developer_admin is None and len(urls) > max_urls:
        urls = urls[:max_urls]

    scraper = MediaScraper()

    def generate():
        import json as _json
        total = len(urls)
        all_images = []
        all_videos = []

        for i, url in enumerate(urls):
            if i > 0 and delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
            yield f"data: {_json.dumps({'type':'progress','index':i,'total':total,'url':url,'status':'processing'})}\n\n"
            try:
                result = scraper.scrape_media(url)
                imgs = result.get('images', [])
                vids = result.get('videos', [])
                all_images.extend(imgs)
                all_videos.extend(vids)
                yield f"data: {_json.dumps({'type':'result','index':i,'total':total,'url':url,'images':imgs,'videos':vids,'ok':True})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'type':'result','index':i,'total':total,'url':url,'images':[],'videos':[],'ok':False,'error':str(exc)})}\n\n"

        final_images = list(dict.fromkeys(all_images))
        final_videos = list(dict.fromkeys(all_videos))
        yield f"data: {_json.dumps({'type':'done','total_images':len(final_images),'total_videos':len(final_videos),'images':final_images,'videos':final_videos})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/debug/report-batch', methods=['POST'])
def debug_report_batch():
    """Recebe falhas do processamento em lote e registra no debug logger."""
    data = request.get_json(silent=True) or {}
    failures = data.get('failures', [])
    if not failures or not isinstance(failures, list):
        return jsonify({'error': 'Nenhuma falha enviada.'}), 400

    logged = 0
    for item in failures:
        url        = str(item.get('url', '')).strip()
        error      = str(item.get('error', 'Erro desconhecido'))
        error_type = str(item.get('error_type', 'request_error'))
        if url:
            debug_logger.log_failed_url(url, error, error_type)
            logged += 1

    return jsonify({'logged': logged, 'message': f'{logged} URL(s) registrada(s) no debug.'})


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN PANEL ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    # Already logged in → redirect to panel
    adm, _ = _get_admin()
    if adm:
        return redirect('/admin/')

    error    = None
    locked   = False
    lock_min = 0
    ip       = _get_client_ip()
    locked, lock_min = admin_store.is_locked(ip)

    geo_info  = None
    ua_str    = None

    if request.method == 'POST' and not locked:
        username = request.form.get('username', '').strip()[:64]
        if username[:1].isupper():
            username = username[0].lower() + username[1:]
        password = request.form.get('password', '')[:128]

        if not username or not password:
            error = 'Preencha usuário e senha.'
        else:
            user = admin_store.authenticate(username, password)
            if user:
                admin_store.clear_failures(ip)
                token = admin_store.create_session(user['id'], ip)
                admin_store.set_status(user['id'], 'online')
                admin_store.log_action(user['id'], user['display_name'], user['role'],
                                       'Login', None, ip)
                # ── Auto-register device fingerprint in WAF whitelist ──────────────
                # Derive a keyed SHA-256 so the raw canvas FP is never
                # stored in the DB.  The WAF derives the same hash on every request
                # and looks it up via is_ip_whitelisted(ip, device_sig).
                _canvas_fp = request.headers.get('X-Canvas-FP', '').strip()[:128]
                _device_sig = _staff_device_signature(_canvas_fp)
                if _device_sig:
                    _role_label = user.get('role', 'staff').upper()
                    admin_store.auto_register_staff_whitelist(
                        nome_dev=f"[{_role_label}] {user['display_name']}",
                        ip=ip,
                        dispositivo_id=_device_sig,
                    )
                # Fire login webhook (geo in background to not block)
                _ua = request.headers.get('User-Agent', '')[:200] or None
                def _fire_login(name, role, _ip, ua):
                    geo = _get_geo(_ip)
                    dwh.fire('admin_login', dwh.build_login_embed(name, role, _ip, geo, ua))
                threading.Thread(target=_fire_login,
                                 args=(user['display_name'], user['role'], ip, _ua),
                                 daemon=True).start()
                resp = redirect('/admin/')
                resp.set_cookie(ADMIN_COOKIE, token,
                                httponly=True, samesite='Strict',
                                max_age=60 * 60 * 8)
                return resp
            else:
                fails = admin_store.record_failure(ip, attempted_username=username)
                locked_now, lock_min = admin_store.is_locked(ip)
                if locked_now:
                    locked = True
                    # Fetch geo + notify owner
                    ua_str = request.headers.get('User-Agent', '')[:120] or 'Desconhecido'
                    geo_info = _get_geo(ip)
                    if geo_info.get('city') and geo_info['city'] != '?':
                        geo_str = f"{geo_info['city']}, {geo_info['region']}, {geo_info['country']}"
                    else:
                        geo_str = 'não disponível'
                    notif_msg = (
                        f'🚨 Brute Force — IP {ip} bloqueado\n'
                        f'👤 Usuário alvo: "{username}"\n'
                        f'🔢 Tentativas: {fails} falhas consecutivas\n'
                        f'📍 Localização: {geo_str}\n'
                        f'💻 Dispositivo: {ua_str[:80]}'
                    )
                    admin_store.add_notification('security', notif_msg)
                    # Fire brute_force webhook (geo already fetched above)
                    _bf_ua = ua_str or ''
                    _bf_geo = geo_info
                    dwh.fire('brute_force',
                             dwh.build_brute_force_embed(ip, fails, _bf_geo, _bf_ua, username=username))
                    # Record forensic incident exactly once at threshold crossing.
                    # Guard against concurrent requests both reaching fails==LOCKOUT_AFTER:
                    # only the request that returned exactly LOCKOUT_AFTER creates the row.
                    if fails == admin_store.LOCKOUT_AFTER:
                        admin_store.record_brute_force_incident(
                            ip, username, fails, _bf_geo, _bf_ua,
                            browser_name=_parse_browser(_bf_ua),
                            operating_system=_parse_os(_bf_ua))
                else:
                    remaining = admin_store.LOCKOUT_AFTER - fails
                    error = f'Usuário ou senha incorretos. {max(0, remaining)} tentativa(s) restante(s).'

    # If already locked from before, also try to fetch geo (for display only)
    if locked and geo_info is None:
        ua_str = request.headers.get('User-Agent', '')[:120] or 'Desconhecido'
        geo_info = _get_geo(ip)

    attempted_username = None
    if locked:
        _linfo = admin_store.get_lockout_info(ip)
        if _linfo:
            attempted_username = _linfo.get('attempted_username')

    return render_template('admin/login.html',
                           error=error, locked=locked, locked_min=lock_min,
                           blocked_ip=ip if locked else None,
                           geo_info=geo_info,
                           ua_str=ua_str,
                           attempted_username=attempted_username)


@app.route('/admin/login/report-hardware', methods=['POST'])
def admin_login_report_hardware():
    ip = _get_client_ip()
    locked, _ = admin_store.is_locked(ip)
    if not locked:
        return jsonify({'ok': False})
    import json as _json
    data = request.get_json(silent=True) or {}
    allowed = {'cores', 'memory', 'platform', 'screen', 'gpu', 'timezone', 'languages'}
    clean = {k: str(v)[:150] for k, v in data.items() if k in allowed and v is not None}
    hw_json = _json.dumps(clean, ensure_ascii=False)
    admin_store.record_hardware(ip, hw_json)
    # Update most-recent brute-force incident with canvas fingerprint + WebRTC IP
    canvas_fp = str(data.get('canvas_fp', ''))[:64]
    webrtc_ip = str(data.get('webrtc_ip', ''))[:64]
    hw_fields = _parse_hardware_fields(hw_json)
    admin_store.update_incident_fingerprint(
        ip, canvas_fp, webrtc_ip, hw_json,
        hardware_ram=hw_fields['hardware_ram'],
        hardware_gpu=hw_fields['hardware_gpu'],
        hardware_cores=hw_fields['hardware_cores'],
        screen_resolution=hw_fields['screen_resolution'],
    )
    return jsonify({'ok': True})


@app.route('/admin/login/request-unlock', methods=['POST'])
def admin_login_request_unlock():
    ip  = _get_client_ip()
    locked, _ = admin_store.is_locked(ip)
    if not locked:
        return jsonify({'ok': False, 'error': 'IP não bloqueado.'})
    ua  = request.headers.get('User-Agent', '')[:300]
    gd = _get_geo(ip)
    if gd.get('city') and gd['city'] != '?':
        geo = f"{gd['city']}, {gd['region']}, {gd['country']} | {gd['org']}"
    else:
        geo = ''
    _linfo = admin_store.get_lockout_info(ip)
    _attempted_username = _linfo.get('attempted_username') if _linfo else None
    _hardware_info = _linfo.get('hardware_info') if _linfo else None
    req_id = admin_store.create_unlock_request(ip, ua, geo, attempted_username=_attempted_username,
                                                hardware_info=_hardware_info)
    if req_id is None:
        return jsonify({'ok': False, 'error': 'Solicitação já está pendente.'})
    admin_store.add_notification(
        'security',
        f'🔓 Solicitação de desbloqueio de IP\n'
        f'IP: {ip}\n'
        f'Localização: {geo or "desconhecida"}\n'
        f'Dispositivo: {ua[:80] or "desconhecido"}\n'
        f'Use /admin/unlock-requests para aprovar ou negar.'
    )
    return jsonify({'ok': True})


@app.route('/admin/unlock-requests')
def admin_unlock_requests():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    pending  = admin_store.get_unlock_requests('pending')
    approved = admin_store.get_unlock_requests('approved', limit=20)
    denied   = admin_store.get_unlock_requests('denied',   limit=20)
    return render_template('admin/unlock_requests.html',
                           admin=admin,
                           role_meta=admin_store.ROLE_META,
                           pending=pending, approved=approved, denied=denied)


@app.route('/admin/unlock-requests/delete', methods=['POST'])
def admin_unlock_delete():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(403)
    req_id = int(request.form.get('req_id', 0))
    admin_store.delete_unlock_request(req_id)
    return redirect('/admin/unlock-requests')


@app.route('/admin/unlock-requests/handle', methods=['POST'])
def admin_unlock_handle():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(403)
    req_id = int(request.form.get('req_id', 0))
    action = request.form.get('action', '')
    if action not in ('approved', 'denied'):
        abort(400)
    result = admin_store.handle_unlock_request(req_id, action, admin['display_name'])
    if result:
        label = 'Aprovado' if action == 'approved' else 'Negado'
        admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                          f'Desbloqueio de IP {label}: {result["ip"]}',
                          f'req_id={req_id}, action={action}',
                          ip=_get_client_ip())
        session['_admin_flash'] = {
            'type': 'success' if action == 'approved' else 'warning',
            'text': f'Solicitação {label.lower()} para IP {result["ip"]}.'
        }
    return redirect('/admin/unlock-requests')


@app.route('/admin/logout')
def admin_logout():
    token = request.cookies.get(ADMIN_COOKIE, '')
    adm   = g.get('admin')
    if not adm and token:
        adm, _ = admin_store.get_session(token)
    if adm:
        _lip = _get_client_ip()
        admin_store.set_status(adm['id'], 'offline')
        admin_store.log_action(adm['id'], adm['display_name'], adm['role'],
                               'Logout', None, _lip)
        dwh.fire('admin_logout',
                 dwh.build_logout_embed(adm['display_name'], adm['role'], _lip))
    if token:
        admin_store.delete_session(token)
    resp = redirect('/admin/login')
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


@app.route('/admin/online-members-api')
def admin_online_members_api():
    """Poll rápido (client-side) do painel de Membros Online, respeitando a
    invisibilidade do Owner para cargos inferiores."""
    admin = g.admin
    members = admin_store.get_online_members(admin['role'])
    return jsonify([{
        'id':           m['id'],
        'username':     m.get('username'),
        'display_name': m['display_name'],
        'role':         m['role'],
        'avatar':       m['avatar'],
        'status':       m['status'],
        'crowned':      bool(m['crowned']),
    } for m in members])


@app.route('/admin/', endpoint='admin_panel_idx')
@app.route('/admin', endpoint='admin_panel')
def admin_panel_view():
    admin = g.admin
    is_mgr = admin_store.has_perm(admin['role'], 'manage_admins')
    maintenance     = admin_store.get_maintenance()
    unread_count    = admin_store.count_unread_notifications(admin['id'], admin['role'])
    online_members  = admin_store.get_online_members(admin['role'])
    flash_msg       = session.pop('_admin_flash', None)
    internal_access_mod    = admin_store.get_internal_access_moderator()
    internal_access_helper = admin_store.get_internal_access_helper()
    all_active = admin_store.list_admins()
    chat_admins = [{'id': a['id'], 'username': a['username'], 'display_name': a['display_name'],
                    'role': a['role'], 'avatar': a['avatar']}
                   for a in all_active
                   if a['active'] and a['id'] != admin['id']]
    suspicious_requests  = admin_store.get_suspicious_requests(limit=100) if admin['role'] == 'owner' else []
    suspicious_today     = admin_store.count_suspicious_today() if admin['role'] == 'owner' else 0
    brute_force_today    = admin_store.count_brute_force_today() if admin['role'] == 'owner' else 0
    pending_unlock_count = admin_store.count_pending_unlock_requests() if is_mgr else 0
    return render_template(
        'admin/panel.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        role_level=admin_store.ROLE_LEVEL,
        has_perm=admin_store.has_perm,
        all_admins=admin_store.list_admins()             if is_mgr else [],
        active_sessions=admin_store.get_active_sessions(viewer_role=admin['role']) if is_mgr else [],
        current_token=request.cookies.get('admin_session', ''),
        log_entries=admin_store.get_activity_log(viewer_role=admin['role'])  if is_mgr else [],
        summary=debug_logger.get_summary(),
        maintenance=maintenance,
        unread_count=unread_count,
        online_members=online_members,
        statuses=admin_store.STATUSES,
        can_terminate=admin_store.can_terminate_session,
        flash_msg=flash_msg,
        payments_disabled=pgw.are_payments_disabled(),
        internal_access_mod=internal_access_mod,
        internal_access_helper=internal_access_helper,
        chat_admins=chat_admins,
        suspicious_requests=suspicious_requests,
        suspicious_today=suspicious_today,
        brute_force_today=brute_force_today,
        crowned_id=admin_store.get_crowned_admin_id(),
        pending_unlock_count=pending_unlock_count,
    )


@app.route('/admin/ranking')
def admin_ranking_view():
    admin = g.admin
    rows  = admin_store.get_ranking()
    tiers = admin_store.get_vip_tiers()
    now = time.time()

    # Intercepta e recalcula os níveis antigos em tempo de execução
    updated_rows = []
    for r in rows:
        r_dict = dict(r)  # Evita erros de objeto protegido contra escrita
        try:
            r_dict['online'] = bool(
                r_dict.get('last_seen_at') and now - float(r_dict['last_seen_at']) <= 120
            )
        except (TypeError, ValueError):
            r_dict['online'] = False
        total = float(r_dict.get('total_donated') or 0.0)
        
        # Enforça a nova hierarquia cronológica estrita de 7 níveis (>=)
        if total >= 500.00:
            r_dict['vip_level'] = 0  # VIP SUPREME
        elif total >= 100.00:
            r_dict['vip_level'] = 1  # VIP DIAMANTE
        elif total >= 80.00:
            r_dict['vip_level'] = 2  # VIP RUBI
        elif total >= 60.00:
            r_dict['vip_level'] = 3  # VIP OURO
        elif total >= 35.00:
            r_dict['vip_level'] = 4  # VIP PRATA
        elif total >= 15.00:
            r_dict['vip_level'] = 5  # VIP BRONZE
        else:
            r_dict['vip_level'] = 6  # VIP ESTELAR

        updated_rows.append(r_dict)

    internal_access_mod    = admin_store.get_internal_access_moderator()
    internal_access_helper = admin_store.get_internal_access_helper()
    all_active  = admin_store.list_admins()
    chat_admins = [{'id': a['id'], 'username': a['username'], 'display_name': a['display_name'],
                    'role': a['role'], 'avatar': a['avatar']}
                   for a in all_active
                   if a['active'] and a['id'] != admin['id']]
    return render_template(
        'admin/ranking.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        rows=updated_rows,
        tiers=tiers,
        internal_access_mod=internal_access_mod,
        internal_access_helper=internal_access_helper,
        chat_admins=chat_admins,
    )

@app.route('/admin/ranking/config/update/<int:tier_id>', methods=['POST'])
def admin_ranking_config_update(tier_id):
    """Owner-only: update a VIP tier's min_value and bonus_urls from the config modal."""
    admin = g.admin
    if admin.get('role') != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    data      = request.get_json(silent=True) or {}
    try:
        min_val    = float(data.get('min_value', 0))
        bonus_urls = int(data.get('bonus_urls', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valores inválidos'}), 400
    if min_val < 0 or bonus_urls < 0:
        return jsonify({'error': 'Valores devem ser positivos'}), 400
    ok = admin_store.update_vip_tier(tier_id, min_val, bonus_urls)
    if not ok:
        return jsonify({'error': 'Tier não encontrado'}), 404
    admin_store.audit(admin['id'], admin['username'], admin['role'], 'vip_tier_update',
                      f'Tier {tier_id}: min_value={min_val}, bonus_urls={bonus_urls}')
    return jsonify({'ok': True})


@app.route('/admin/ranking/create-manual', methods=['POST'])
def admin_ranking_create_manual():
    """Owner-only: insert a manually-created donation directly into the ranking."""
    import uuid as _uuid, json as _json
    admin = g.admin
    if admin.get('role') != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403

    data      = request.get_json(silent=True) or {}
    nickname  = str(data.get('nickname', '')).strip()[:40]
    avatar_id = str(data.get('avatar_id', 'm_1')).strip().lower()
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valor inválido'}), 400

    if not nickname or len(nickname) < 2:
        return jsonify({'error': 'Apelido inválido (mínimo 2 caracteres)'}), 400
    if amount < 0.01:
        return jsonify({'error': 'Valor deve ser maior que R$ 0,00'}), 400
    if avatar_id not in donor_avatars.get_valid_avatar_ids():
        return jsonify({'error': 'Avatar inválido'}), 400

    token = str(_uuid.uuid4())
    try:
        result = admin_store.register_donation(
            nickname, avatar_id, amount, token,
            payment_id=None,
            ip=request.remote_addr or '',
            ua=request.headers.get('User-Agent', ''),
        )
    except Exception as exc:
        app_logger.error(f'admin_ranking_create_manual error: {exc}')
        return jsonify({'error': 'Erro interno ao registrar doação'}), 500

    # Broadcast Breaking News ticker to homepage
    stats_store.set_val('latest_ranking_event', _json.dumps({
        'nickname':           nickname,
        'avatar_id':          avatar_id,
        'vip_level':          result['vip_level'],
        'discriminator':      result.get('discriminator', ''),
        'is_new':             result.get('is_new', True),
        'previous_vip_level': result.get('previous_vip_level'),
        'amount':             amount,
        'ts':                 time.time(),
    }))

    admin_store.audit(
        admin['id'], admin['username'], admin['role'], 'manual_donation_created',
        f'nick={nickname} amount={amount:.2f} avatar={avatar_id} vip={result["vip_level"]}'
    )

    return jsonify({
        'ok':           True,
        'vip_level':    result['vip_level'],
        'total_donated': result['total_donated'],
        'nickname':     nickname,
        'avatar_id':    avatar_id,
    })


@app.route('/api/donation/register', methods=['POST'])
def donation_register():
    import uuid as _uuid
    data       = request.get_json(silent=True) or {}
    style      = (
        _clean_donor_style(data)
        if any(key in data for key in ('font_id', 'text_effect', 'name_color'))
        else {'font_id': None, 'text_effect': None, 'name_color': None}
    )
    payment_id = str(data.get('payment_id', '')).strip()
    is_returning = bool(data.get('returning', False))

    if not payment_id:
        return jsonify({'error': 'Pagamento não identificado'}), 400

    # ── Server-side payment verification — amount comes ONLY from our DB ──
    payment_row = admin_store.get_pix_payment_by_id(payment_id)
    if not payment_row or payment_row.get('status') != 'paid':
        return jsonify({'error': 'Pagamento não confirmado ou não encontrado'}), 403

    amount    = float(payment_row['amount'])
    token     = request.cookies.get('donation_token', '').strip() or str(_uuid.uuid4())
    client_ip = request.remote_addr or ''
    client_ua = request.headers.get('User-Agent', '')

    # ── Returning donor shortcut: reuse stored nickname+avatar ──
    if is_returning and token:
        existing = admin_store.get_vip_by_token(token)
        if existing:
            # Fetch full row for nickname+avatar
            donor_row = admin_store.check_returning_donor(token, client_ip, client_ua)
            if donor_row:
                nickname  = donor_row['nickname']
                avatar_id = donor_row['avatar_id']
                # Gender-locked avatar refresh: a returning supporter may pick a NEW
                # avatar from the success-modal grid, but only within their own
                # already-recorded gender family (donor_m_* -> 'm', donor_f_* -> 'f').
                # Any cross-gender or invalid id is silently ignored, keeping the
                # stored avatar — this is enforced server-side, never trusted from client.
                requested_avatar = str(data.get('avatar_id', '')).strip().lower()
                if requested_avatar and requested_avatar in donor_avatars.get_valid_avatar_ids():
                    existing_gender  = avatar_id.split('_', 1)[0]
                    requested_gender = requested_avatar.split('_', 1)[0]
                    if requested_gender == existing_gender:
                        avatar_id = requested_avatar
                try:
                    result = admin_store.register_donation(nickname, avatar_id, amount, token,
                                                           payment_id=payment_id,
                                                       ip=client_ip, ua=client_ua,
                                                        font_id=style['font_id'],
                                                        text_effect=style['text_effect'],
                                                         name_color=style['name_color'],
                                                         currency=payment_row.get('currency', 'BRL'))
                except admin_store.DuplicatePaymentError:
                    return jsonify({'error': 'Este pagamento já foi vinculado a outro perfil.'}), 409
                except Exception as exc:
                    app_logger.error(f'donation_register (returning) error: {exc}')
                    return jsonify({'error': 'Erro interno'}), 500
                import json as _json
                stats_store.set_val('latest_ranking_event', _json.dumps({
                    'nickname':           nickname,
                    'avatar_id':          avatar_id,
                    'vip_level':          result['vip_level'],
                    'discriminator':      result.get('discriminator', ''),
                    'is_new':             result.get('is_new', True),
                    'previous_vip_level': result.get('previous_vip_level'),
                    'amount':             amount,
                    'ts':                 time.time(),
                }))
                _gw_ctx = payment_row.get('gateway_id', 'Manual')
                if result.get('is_new') is False:
                    _fire_repeat_donation_alert(
                        nickname,
                        amount=amount,
                        gateway=_gw_ctx,
                        payment_id=payment_id,
                        access_key=token,
                    )
                else:
                    try:
                        dwh.fire('payment', dwh.build_payment_embed(
                            action='novo_doador',
                            description=f'🎉 **{nickname}** acaba de entrar para o Ranking de Apoiadores! Bem-vindo! 🏆',
                            amount=amount,
                            gateway=_gw_ctx,
                            payment_id=payment_id,
                            access_key=token,
                        ))
                    except Exception:
                        pass
                resp = jsonify({'ok': True, 'token': token, 'vip_level': result['vip_level'],
                                'total_donated': result['total_donated'], 'nickname': nickname,
                                'avatar_id': avatar_id, 'discriminator': result.get('discriminator', ''),
                                'previous_vip_level': result.get('previous_vip_level'), 'amount': amount})
                resp.set_cookie('donation_token', token, max_age=365*24*3600, samesite='Lax', httponly=False, path='/')
                return resp
        # Token not verified — fall through to standard new-donor registration

    nickname  = str(data.get('nickname', '')).strip()[:40]
    avatar_id = str(data.get('avatar_id', 'm_1')).strip().lower()

    if not nickname or len(nickname) < 2:
        return jsonify({'error': 'Apelido inválido'}), 400
    # Gender-prefixed avatar ids: donor_m_*.png -> masculino, donor_f_*.png -> feminino.
    # The valid set is scanned live from static/donors/, so newly-added avatar
    # files are accepted immediately without any code change.
    if avatar_id not in donor_avatars.get_valid_avatar_ids():
        return jsonify({'error': 'Avatar inválido'}), 400

    try:
        # Atomically claim payment + upsert ranking in one transaction
        result = admin_store.register_donation(nickname, avatar_id, amount, token,
                                               payment_id=payment_id,
                                               ip=client_ip, ua=client_ua,
                                               font_id=style['font_id'],
                                               text_effect=style['text_effect'],
                                               name_color=style['name_color'],
                                               currency=payment_row.get('currency', 'BRL'))
    except admin_store.DuplicatePaymentError:
        return jsonify({'error': 'Este pagamento já foi vinculado a outro perfil.'}), 409
    except Exception as exc:
        app_logger.error(f'donation_register error: {exc}')
        return jsonify({'error': 'Erro interno'}), 500

    # ── Broadcast ticker event for all connected users ──
    import json as _json
    stats_store.set_val('latest_ranking_event', _json.dumps({
        'nickname':           nickname,
        'avatar_id':          avatar_id,
        'vip_level':          result['vip_level'],
        'discriminator':      result.get('discriminator', ''),
        'is_new':             result.get('is_new', True),
        'previous_vip_level': result.get('previous_vip_level'),
        'amount':             amount,
        'ts':                 time.time(),
    }))

    _gw_ctx2 = payment_row.get('gateway_id', 'Manual')
    if result.get('is_new') is False:
        _fire_repeat_donation_alert(
            nickname,
            amount=amount,
            gateway=_gw_ctx2,
            payment_id=payment_id,
            access_key=token,
        )
    else:
        try:
            dwh.fire('payment', dwh.build_payment_embed(
                action='novo_doador',
                description=f'🎉 **{nickname}** acaba de entrar para o Ranking de Apoiadores! Bem-vindo! 🏆',
                amount=amount,
                gateway=_gw_ctx2,
                payment_id=payment_id,
                access_key=token,
            ))
        except Exception:
            pass

    resp = jsonify({
        'ok':                 True,
        'token':              token,
        'vip_level':          result['vip_level'],
        'total_donated':      result['total_donated'],
        'nickname':           nickname,
        'avatar_id':          avatar_id,
        'discriminator':      result.get('discriminator', ''),
        'previous_vip_level': result.get('previous_vip_level'),
        'amount':             amount,
    })
    resp.set_cookie('donation_token', token,
                    max_age=365 * 24 * 3600, samesite='Lax', httponly=False, path='/')
    return resp


@app.route('/api/donor-avatars')
def api_donor_avatars():
    """
    Live donor avatar catalog, scanned from static/donors/ on every request.
    Lets the frontend gender toggle/avatar grid stay in sync automatically
    whenever donor_m_*.png / donor_f_*.png files are added or removed —
    no code change or redeploy needed.
    """
    male, female = donor_avatars.scan_avatar_ids()
    return jsonify({'m': male, 'f': female})


@app.route('/api/public/ranking')
def api_public_ranking():
    """Public JSON endpoint for the ranking overlay modal."""
    rows = admin_store.get_ranking(limit=20)
    now = time.time()
    for row in rows:
        last_seen = row.get('last_seen_at')
        try:
            row['online'] = bool(last_seen and now - float(last_seen) <= 120)
        except (TypeError, ValueError):
            row['online'] = False
    return jsonify(rows)


@app.route('/api/ranking/latest')
def api_ranking_latest():
    """Returns the latest ranking ticker event for client-side polling."""
    import json as _json
    raw = stats_store.get_real().get('latest_ranking_event')
    if raw:
        try:
            event = _json.loads(raw)
            resp = jsonify({'ok': True, 'event': event})
        except Exception:
            resp = jsonify({'ok': False})
    else:
        resp = jsonify({'ok': False})
    resp.headers['Cache-Control'] = 'no-store, no-cache'
    return resp


@app.route('/api/ping')
def api_ping():
    """Returns keep-alive ping timing data for the dashboard countdown."""
    _ensure_auto_ping()
    _ensure_next_ping_timestamp()
    _real = stats_store.get_real()
    next_ping_ts = _real.get('next_ping')
    if next_ping_ts:
        remaining_seconds = max(0, int(next_ping_ts - time.time()))
        if remaining_seconds == 0:
            remaining_seconds = 420  # full interval fallback while job hasn't re-fired yet
    else:
        remaining_seconds = 420  # job hasn't run yet — show full interval
    resp = jsonify({
        'success':           True,
        'next_ping_ts':      next_ping_ts,
        'remaining_seconds': remaining_seconds,
        'last_latency_ms':   _real.get('last_ping_latency'),
    })
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/admin/ranking/delete/<int:user_id>', methods=['POST'])
def admin_ranking_delete(user_id):
    """Owner-only: delete a ranking entry. Returns JSON so the frontend can remove the row without a reload."""
    admin = g.admin
    if admin.get('role') != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    admin_store.delete_ranking_entry(user_id)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'], 'ranking_delete',
                      f'Deletou entrada do ranking id={user_id}')
    return jsonify({'ok': True})


@app.route('/admin/payments/update-status/<payment_id>', methods=['POST'])
def admin_payments_update_status(payment_id):
    """Admin/owner: update payment status via AJAX (pencil modal)."""
    admin = g.admin
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Sem permissão'}), 403
    data   = request.get_json(silent=True) or {}
    status = str(data.get('status', '')).strip().lower()
    if status not in ('paid', 'pending', 'expired'):
        return jsonify({'error': 'Status inválido'}), 400
    admin_store.update_pix_payment_status(payment_id, status, admin['display_name'])
    admin_store.audit(admin['id'], admin['display_name'], admin['role'], 'payment_status_update',
                      f'PIX {payment_id} → {status}')
    try:
        dwh.fire('payment', dwh.build_payment_embed(
            action='payment_status_update',
            description=f'Status do pagamento atualizado para **{status}** por `{admin["display_name"]}`.',
            gateway=admin['display_name'],
            payment_id=payment_id,
        ))
    except Exception:
        pass
    return jsonify({'ok': True, 'status': status})


@app.route('/api/check-returning-donor', methods=['GET'])
def api_check_returning_donor():
    """Check if the donation_token cookie belongs to a verified returning donor.
    Validates token against stored IP + User-Agent to prevent session hijacking."""
    token = request.cookies.get('donation_token', '').strip()
    if not token:
        return jsonify({'returning': False})
    ip = request.remote_addr or ''
    ua = request.headers.get('User-Agent', '')
    donor = admin_store.check_returning_donor(token, ip, ua)
    if not donor:
        return jsonify({'returning': False})
    return jsonify({
        'returning':     True,
        'nickname':      donor.get('nickname', ''),
        'avatar_id':     donor.get('avatar_id', ''),
        'font_id':       donor.get('font_id', 'default'),
        'text_effect':   donor.get('text_effect', 'solid'),
        'name_color':    donor.get('name_color', '#f3f4f6'),
        'vip_level':     donor.get('vip_level', 5),
        'total_donated': float(donor.get('total_donated', 0)),
    })


@app.route('/api/supporter/presence', methods=['POST'])
def api_supporter_presence():
    """Heartbeat for the public supporter presence indicator.

    The cookie is the normal session source. The localStorage copy is accepted
    as a same-origin fallback because browsers/extensions can block or clear a
    non-HttpOnly cookie while keeping localStorage intact.
    """
    data = request.get_json(silent=True) or {}
    token = (
        request.cookies.get('donation_token', '').strip()
        or str(data.get('token', '')).strip()
    )
    if not token:
        return jsonify({'ok': False}), 204
    try:
        admin_store.touch_supporter_presence(token)
    except Exception as exc:
        app_logger.warning(f'presence heartbeat failed: {exc}')
        return jsonify({'ok': False}), 503
    return jsonify({'ok': True})


@app.route('/api/supporter/style', methods=['POST'])
def api_supporter_style():
    """Update only the authenticated supporter's public nickname style."""
    token = request.cookies.get('donation_token', '').strip()
    if not token:
        return jsonify({'error': 'Apoiador não identificado'}), 401
    style = _clean_donor_style(request.get_json(silent=True) or {})
    if not admin_store.update_donor_style(token, **style):
        return jsonify({'error': 'Apoiador não encontrado'}), 404
    return jsonify({'ok': True, **style})


@app.route('/ranking')
def public_ranking_view():
    """Public leaderboard — accessible without admin auth."""
    from datetime import datetime as _dt
    rows = admin_store.get_ranking()
    now = time.time()
    for row in rows:
        # Recalculate from the accumulated amount so old/stale persisted
        # vip_level values cannot show the wrong badge on the public page.
        total = float(row.get('total_donated') or 0.0)
        if total >= 500.00:
            row['vip_level'] = 0  # VIP SUPREME
        elif total >= 100.00:
            row['vip_level'] = 1  # VIP DIAMANTE
        elif total >= 80.00:
            row['vip_level'] = 2  # VIP RUBI
        elif total >= 60.00:
            row['vip_level'] = 3  # VIP OURO
        elif total >= 35.00:
            row['vip_level'] = 4  # VIP PRATA
        elif total >= 15.00:
            row['vip_level'] = 5  # VIP BRONZE
        else:
            row['vip_level'] = 6  # VIP ESTELAR
        try:
            row['online'] = bool(row.get('last_seen_at') and now - float(row['last_seen_at']) <= 120)
            if row.get('last_seen_at'):
                row['last_seen_label'] = _dt.fromtimestamp(
                    float(row['last_seen_at'])
                ).strftime('%d/%m/%Y %H:%M')
        except (TypeError, ValueError):
            row['online'] = False
            row['last_seen_label'] = ''
    return render_template('ranking.html', rows=rows)


@app.route('/admin/settings')
def admin_settings_view():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    maintenance = admin_store.get_maintenance()
    flash_msg   = session.pop('_admin_flash', None)
    return render_template(
        'admin/settings.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        maintenance=maintenance,
        flash_msg=flash_msg,
    )


@app.route('/admin/manage')
def admin_manage_view():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    flash_msg = session.pop('_admin_flash', None)
    return render_template(
        'admin/manage.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        all_admins=admin_store.list_admins(),
        can_manage=admin_store.can_manage,
        role_avatar_map=admin_store.ROLE_AVATAR_MAP,
        flash_msg=flash_msg,
    )


@app.route('/admin/api/update-permissions/<int:target_id>', methods=['POST'])
def admin_update_permissions(target_id):
    """Only the Owner may update individual permission flags."""
    admin = g.admin
    if admin.get('role') != 'owner':
        return jsonify({'ok': False, 'error': 'Apenas o Owner pode alterar permissões.'}), 403
    data = request.get_json(silent=True) or {}
    permission = str(data.get('permission', '')).strip()
    if permission not in admin_store.ADMIN_PERMISSION_COLUMNS:
        return jsonify({'ok': False, 'error': 'Permissão inválida.'}), 400
    target = admin_store.get_admin_by_id(target_id)
    if not target or target['id'] == admin['id']:
        return jsonify({'ok': False, 'error': 'Usuário não pode ser alterado.'}), 404
    if not admin_store.can_manage(admin['role'], target['role']):
        return jsonify({'ok': False, 'error': 'Sem permissão para este usuário.'}), 403
    enabled = bool(data.get('enabled', False))
    permissions = admin_store.update_admin_permission(target_id, permission, enabled)
    if permissions is None:
        return jsonify({'ok': False, 'error': 'Usuário não encontrado.'}), 404
    admin_store.audit(
        admin['id'], admin['display_name'], admin['role'],
        f'Permissão atualizada: @{target["username"]}',
        f'{permission}={"ativada" if enabled else "desativada"}',
        ip=_get_client_ip())
    return jsonify({'ok': True, 'permissions': permissions})


@app.route('/admin/api/staff-device', methods=['POST'])
def admin_register_staff_device():
    """Register the current staff device in the WAF whitelist after login.

    The endpoint receives hardware hints only to prove this is the browser
    registration flow; only the keyed canvas signature is persisted.
    """
    admin = g.admin
    data = request.get_json(silent=True) or {}
    canvas_fp = str(data.get('canvas_fp', '')).strip()[:128]
    if not re.fullmatch(r'cnv_[a-z0-9]{4,64}', canvas_fp, re.I):
        return jsonify({'ok': False, 'error': 'Fingerprint de dispositivo inválido.'}), 400

    device_sig = _staff_device_signature(canvas_fp)
    if not device_sig:
        return jsonify({'ok': False, 'error': 'Não foi possível identificar este dispositivo.'}), 400

    role_label = str(admin.get('role', 'staff')).upper()
    created = admin_store.auto_register_staff_whitelist(
        nome_dev=f'[{role_label}] {admin.get("display_name", "Equipe")}',
        ip=_get_client_ip(),
        dispositivo_id=device_sig,
    )
    admin_store.log_action(
        admin['id'], admin['display_name'], admin['role'],
        'Dispositivo da equipe identificado',
        'Fingerprint protegido e sincronizado com o WAF',
        _get_client_ip(),
    )
    return jsonify({'ok': True, 'registered': bool(created)})


@app.route('/admin/manage/create', methods=['POST'])
def admin_manage_create():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    username     = request.form.get('username', '').strip()[:32]
    display_name = request.form.get('display_name', '').strip()[:50]
    password     = request.form.get('password', '')[:128]
    role         = request.form.get('role', 'helper')
    # Enforce role hierarchy
    if role not in admin_store.ROLES or role == 'owner':
        role = 'helper'
    if role == 'admin' and admin['role'] != 'owner':
        role = 'moderator'
    allowed_avs = admin_store.ROLE_AVATAR_MAP.get(role, admin_store._ROLE_AVATAR_DEFAULT)
    avatar = request.form.get('avatar', allowed_avs[0])
    if avatar not in allowed_avs:
        avatar = allowed_avs[0]
    if not username or not display_name or len(password) < 8:
        session['_admin_flash'] = {'type': 'danger',
                                   'text': 'Dados inválidos. Senha mínima 8 caracteres.'}
        return redirect('/admin/manage')
    try:
        admin_store.create_admin(username, password, role, display_name, avatar,
                                  admin['display_name'])
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               f'Admin criado: @{username}', f'cargo={role}',
                               _get_client_ip())
        session['_admin_flash'] = {'type': 'success',
                                   'text': f'Admin @{username} criado com sucesso.'}
    except Exception as exc:
        session['_admin_flash'] = {'type': 'danger', 'text': f'Erro: {str(exc)[:100]}'}
    return redirect('/admin/manage')


@app.route('/admin/manage/update', methods=['POST'])
def admin_manage_update():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    try:
        target_id    = int(request.form.get('admin_id', 0))
        display_name = request.form.get('display_name', '').strip()[:50]
        new_role     = request.form.get('role', 'helper')
        new_avatar   = request.form.get('avatar', 'av1')
        active       = request.form.get('active') == '1'
        new_password = request.form.get('new_password', '')[:128]
        new_username = request.form.get('new_username', '').strip()[:32]

        target = admin_store.get_admin_by_id(target_id)
        if not target:
            abort(404)
        self_edit = (admin['id'] == target_id)
        if not self_edit and not admin_store.can_manage(admin['role'], target['role']):
            abort(404)
        # Self-edit: lock role and active to prevent self-promotion/self-deactivation
        if self_edit:
            new_role = target['role']
            active   = True
        else:
            if new_role not in admin_store.ROLES or new_role == 'owner':
                new_role = target['role']
            if not admin_store.can_manage(admin['role'], new_role):
                new_role = target['role']

        # Validate avatar matches new role
        allowed_avs = admin_store.ROLE_AVATAR_MAP.get(new_role, admin_store._ROLE_AVATAR_DEFAULT)
        if new_avatar not in allowed_avs:
            new_avatar = allowed_avs[0]

        # Anti-abuse check (item 15)
        abuse_count = admin_store.check_abuse(admin['id'])
        if abuse_count >= admin_store.ABUSE_THRESHOLD:
            session['_admin_flash'] = {'type': 'danger',
                                       'text': f'Limite de ações críticas atingido ({abuse_count} nos últimos 3 min). Aguarde antes de continuar.'}
            return redirect('/admin/manage')

        # Capture before state for change history (item 12)
        _rl = admin_store.ROLE_META
        before_state = f'cargo={_rl[target["role"]]["label"]}, ativo={"Sim" if target["active"] else "Não"}, nome={target["display_name"]}'

        admin_store.update_role(target_id, new_role)
        admin_store.update_avatar(target_id, new_avatar)
        admin_store.toggle_active(target_id, active)
        with admin_store._lock, admin_store._conn() as c:
            c.execute("UPDATE admins SET display_name=%s WHERE id=%s",
                      (display_name, target_id))
        min_pw_len = 1 if admin['role'] == 'owner' else 8
        if new_password and len(new_password) >= min_pw_len:
            admin_store.update_password(target_id, new_password)

        # Owner-only: update username
        if admin['role'] == 'owner' and new_username and new_username != target['username']:
            import re as _re
            if _re.match(r'^[a-zA-Z0-9_]+$', new_username):
                admin_store.update_username(target_id, new_username)

        after_state = f'cargo={_rl[new_role]["label"]}, ativo={"Sim" if active else "Não"}, nome={display_name}'

        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               f'Admin atualizado: @{target["username"]}',
                               f'role={new_role}, active={active}', _get_client_ip())
        # Immutable audit with before/after (item 12)
        admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                          f'Admin atualizado: @{target["username"]}',
                          f'cargo={new_role}, ativo={active}',
                          before_val=before_state, after_val=after_state,
                          ip=_get_client_ip())
        session['_admin_flash'] = {'type': 'success',
                                   'text': f'Admin @{target["username"]} atualizado.'}
    except Exception as exc:
        session['_admin_flash'] = {'type': 'danger', 'text': f'Erro: {str(exc)[:100]}'}
    return redirect('/admin/manage')


@app.route('/admin/manage/delete', methods=['POST'])
def admin_manage_delete():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    try:
        target_id = int(request.form.get('admin_id', 0))
        target    = admin_store.get_admin_by_id(target_id)
        if not target or not admin_store.can_manage(admin['role'], target['role']):
            abort(404)
        if target['role'] == 'owner':
            abort(404)
        admin_store.delete_admin(target_id)
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               f'Admin excluído: @{target["username"]}', None,
                               _get_client_ip())
        session['_admin_flash'] = {'type': 'success',
                                   'text': f'Admin @{target["username"]} excluído.'}
    except Exception as exc:
        session['_admin_flash'] = {'type': 'danger', 'text': f'Erro: {str(exc)[:100]}'}
    return redirect('/admin/manage')


@app.route('/admin/manage/crown', methods=['POST'])
def admin_manage_crown():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    try:
        target_id = int(request.form.get('admin_id', 0))
        target = admin_store.get_admin_by_id(target_id)
        if not target or target['id'] == admin['id']:
            abort(404)
        has_crown = admin_store.toggle_crown(target_id)
        admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                         'Coroa ' + ('concedida a' if has_crown else 'removida de') + f' @{target["username"]}',
                         'Funcionário do Mês', ip=_get_client_ip())
        return jsonify({'ok': True, 'crowned': has_crown, 'name': target['display_name']})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@app.route('/admin/activity/json')
def admin_activity_json():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    return jsonify(admin_store.get_activity_log(limit=1000))


@app.route('/admin/activity/export')
def admin_activity_export():
    import json as _json
    from datetime import datetime as _dt
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    entries = admin_store.get_activity_log(limit=5000, viewer_role=admin['role'])
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Log exportado', f'{len(entries)} entradas', _get_client_ip())
    now_str = _dt.now().strftime('%d-%m-%Y_%H-%M-%S')

    # ── Batch geo lookup: one API call per unique IP ─────────────────────────
    _GEO_CACHE: dict = {}
    _ROLE_PT = {
        'owner':     'Proprietário',
        'admin':     'Administrador',
        'moderator': 'Moderador',
        'helper':    'Ajudante',
    }
    for e in entries:
        ip = e.get('ip') or ''
        if ip and ip not in _GEO_CACHE:
            try:
                _GEO_CACHE[ip] = _get_geo(ip)
            except Exception:
                _GEO_CACHE[ip] = {}

    # ── Build enriched entries ───────────────────────────────────────────────
    enriched = []
    for e in entries:
        ip  = e.get('ip') or ''
        geo = _GEO_CACHE.get(ip, {})
        ts_raw = e.get('ts') or ''
        enriched.append({
            'id':           e.get('id'),
            'data_hora':    admin_store.fmt_ts(ts_raw),
            'ts_iso':       ts_raw,
            'admin_id':     e.get('admin_id'),
            'admin_nome':   e.get('admin_name'),
            'cargo':        e.get('role'),
            'cargo_label':  _ROLE_PT.get(e.get('role', ''), e.get('role', '')),
            'acao':         e.get('action'),
            'detalhe':      e.get('detail'),
            'ip':           ip or None,
            'geo': {
                'cidade':       geo.get('city', '?')        if geo else None,
                'regiao':       geo.get('region', '?')      if geo else None,
                'pais':         geo.get('country', '?')     if geo else None,
                'codigo_pais':  geo.get('country_code', '') if geo else None,
                'latitude':     geo.get('lat', '')          if geo else None,
                'longitude':    geo.get('lon', '')          if geo else None,
                'provedor':     geo.get('isp', '')          if geo else None,
                'asn':          geo.get('asn', '')          if geo else None,
                'proxy_vpn':    geo.get('is_proxy', False)  if geo else None,
                'datacenter':   geo.get('is_hosting', False)if geo else None,
            } if ip else None,
            'ocultado_em':  admin_store.fmt_ts(e.get('hidden_at')) if e.get('hidden_at') else None,
            'ocultado_por': e.get('hidden_by'),
        })

    data = _json.dumps({
        'exportado_em':    admin_store.fmt_ts(_dt.now().isoformat()),
        'exportado_por':   admin['display_name'],
        'cargo_exportador': _ROLE_PT.get(admin['role'], admin['role']),
        'total_entradas':  len(enriched),
        'entradas':        enriched,
    }, ensure_ascii=False, indent=2)
    return Response(data, mimetype='application/json',
                    headers={'Content-Disposition': f'attachment; filename="logs_{now_str}.json"'})


@app.route('/admin/profile')
def admin_profile_view():
    admin     = g.admin
    flash_msg = session.pop('_profile_flash', None)
    role_avatars = admin_store.ROLE_AVATAR_MAP.get(admin['role'], admin_store._ROLE_AVATAR_DEFAULT)
    return render_template('admin/profile.html', admin=admin,
                           role_meta=admin_store.ROLE_META,
                           role_avatars=role_avatars,
                           flash_msg=flash_msg)


@app.route('/admin/profile/avatar', methods=['POST'])
def admin_profile_avatar():
    admin  = g.admin
    avatar = request.form.get('avatar', '').strip()
    allowed = admin_store.ROLE_AVATAR_MAP.get(admin['role'], admin_store._ROLE_AVATAR_DEFAULT)
    if avatar not in allowed:
        session['_profile_flash'] = {'type': 'danger', 'text': 'Avatar não permitido para seu cargo.'}
    else:
        admin_store.update_avatar(admin['id'], avatar)
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               'Avatar alterado', avatar, _get_client_ip())
        session['_profile_flash'] = {'type': 'success', 'text': 'Avatar atualizado!'}
    return redirect('/admin/profile')


@app.route('/admin/profile/password', methods=['POST'])
def admin_profile_password():
    admin   = g.admin
    current = request.form.get('current_password', '')[:128]
    new_pw  = request.form.get('new_password', '')[:128]
    confirm = request.form.get('confirm_password', '')[:128]
    user = admin_store.authenticate(admin['username'], current)
    if not user:
        session['_profile_flash'] = {'type': 'danger', 'text': 'Senha atual incorreta.'}
    elif len(new_pw) < 8:
        session['_profile_flash'] = {'type': 'danger',
                                     'text': 'Nova senha deve ter ao menos 8 caracteres.'}
    elif new_pw != confirm:
        session['_profile_flash'] = {'type': 'danger',
                                     'text': 'Confirmação de senha não confere.'}
    else:
        admin_store.update_password(admin['id'], new_pw)
        token = request.cookies.get(ADMIN_COOKIE, '')
        if token:
            admin_store.delete_session(token)
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               'Senha alterada', None, _get_client_ip())
        resp = redirect('/admin/login')
        resp.delete_cookie(ADMIN_COOKIE)
        return resp
    return redirect('/admin/profile')


# ═══════════════════════════════════════════════════════════════════════════
#  NEW FEATURE ROUTES (items 5-17)
# ═══════════════════════════════════════════════════════════════════════════

# ── Item 6: Status management ─────────────────────────────────────────────

@app.route('/admin/status', methods=['POST'])
def admin_set_status():
    admin = g.admin
    status = request.form.get('status', '').strip()
    allowed = list(admin_store.STATUSES.keys())
    # Only owner can set 'invisible'
    if status == 'invisible' and admin['role'] != 'owner':
        session['_admin_flash'] = {'type': 'danger', 'text': 'Somente o Owner pode ficar invisível.'}
        return redirect(request.referrer or '/admin/')
    if status not in allowed:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Status inválido.'}
        return redirect(request.referrer or '/admin/')
    admin_store.set_status(admin['id'], status)
    return redirect(request.referrer or '/admin/')


# ── Item 8: Session termination ───────────────────────────────────────────

@app.route('/admin/sessions/terminate', methods=['POST'])
def admin_terminate_session():
    admin = g.admin
    token  = request.form.get('token', '').strip()
    reason = request.form.get('reason', '').strip()
    if not reason:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Motivo é obrigatório.'}
        return redirect('/admin/')
    # Find session owner
    all_sessions = admin_store.get_active_sessions()
    target_sess = next((s for s in all_sessions if s['token'] == token), None)
    if not target_sess:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Sessão não encontrada.'}
        return redirect('/admin/')
    # Don't let someone terminate their own session this way
    if target_sess['id'] == admin['id']:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Use o botão Sair para encerrar sua própria sessão.'}
        return redirect('/admin/')
    # Hierarchy check
    if not admin_store.can_terminate_session(admin['role'], target_sess['role']):
        session['_admin_flash'] = {'type': 'danger', 'text': 'Permissão insuficiente para encerrar esta sessão.'}
        return redirect('/admin/')
    # Owner terminating → no log. Admin terminating → log it.
    admin_store.terminate_session_by_token(token)
    if admin['role'] != 'owner':
        admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                               'Sessão encerrada',
                               f'Usuário: {target_sess["display_name"]} | Motivo: {reason}',
                               _get_client_ip())
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Sessão encerrada',
                      f'Alvo: {target_sess["display_name"]} ({target_sess["role"]}) | Motivo: {reason}',
                      ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success',
                               'text': f'Sessão de {target_sess["display_name"]} encerrada.'}
    return redirect('/admin/')


# ── Item 5: Log visibility (hide/unhide/delete) ───────────────────────────

@app.route('/admin/logs/hide', methods=['POST'])
def admin_log_hide():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    entry_id = int(request.form.get('entry_id', 0))
    reason   = request.form.get('reason', '').strip()
    if not reason:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Motivo é obrigatório para ocultar log.'}
        return redirect('/admin/')
    admin_store.hide_log_entry(entry_id, admin['id'])
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Log ocultado', f'entry_id={entry_id} | Motivo: {reason}',
                      ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': 'Entrada ocultada da visualização dos subordinados.'}
    return redirect('/admin/')


@app.route('/admin/logs/delete', methods=['POST'])
def admin_log_delete():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    entry_ids_raw = request.form.getlist('entry_ids')
    entry_ids = []
    for eid in entry_ids_raw:
        try:
            entry_ids.append(int(eid))
        except ValueError:
            pass
    if entry_ids:
        admin_store.delete_log_entries(entry_ids)
    session['_admin_flash'] = {'type': 'success', 'text': f'{len(entry_ids)} entrada(s) removida(s).'}
    return redirect('/admin/')


@app.route('/admin/logs/clear', methods=['POST'])
def admin_log_clear():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    reason = request.form.get('reason', '').strip()
    if not reason:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Motivo é obrigatório para limpar todos os logs.'}
        return redirect('/admin/')
    admin_store.clear_all_logs()
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Todos os logs apagados', f'Motivo: {reason}', ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': 'Todos os logs de atividade foram apagados.'}
    return redirect('/admin/')


# ── Item 10: Audit log ────────────────────────────────────────────────────

@app.route('/admin/audit')
def admin_audit_view():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    entries = admin_store.get_audit_log(limit=500)
    flash_msg = session.pop('_admin_flash', None)
    return render_template('admin/audit.html', admin=admin, entries=entries,
                           role_meta=admin_store.ROLE_META, flash_msg=flash_msg)


# ── Item 14: Maintenance mode ─────────────────────────────────────────────

@app.route('/admin/maintenance', methods=['POST'])
def admin_maintenance_toggle():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    reason = request.form.get('reason', '').strip()
    if not reason:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Motivo é obrigatório para alterar modo de manutenção.'}
        return redirect(request.referrer or '/admin/')
    enabled  = request.form.get('enabled') == '1'
    message  = request.form.get('message', 'Site em manutenção. Voltamos em breve.').strip()[:300]
    admin_store.set_maintenance(enabled, message, admin['display_name'])
    status_str = 'ativado' if enabled else 'desativado'
    _mip = _get_client_ip()
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      f'Modo manutenção {status_str}',
                      f'Motivo: {reason} | Mensagem: {message}', ip=_mip)
    dwh.fire('maintenance',
             dwh.build_maintenance_embed(enabled, message, admin['display_name'], _mip))
    session['_admin_flash'] = {'type': 'success' if not enabled else 'warning',
                               'text': f'Modo manutenção {status_str}.'}
    return redirect('/admin/settings')


@app.route('/admin/maintenance/message', methods=['POST'])
def admin_maintenance_message():
    """Update only the maintenance message text, without toggling state or requiring a reason."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'error': 'Não autorizado'}), 403
    data    = request.get_json(silent=True) or {}
    message = str(data.get('message', '')).strip()[:300]
    if not message:
        return jsonify({'error': 'Mensagem vazia'}), 400
    current = admin_store.get_maintenance()
    enabled = bool(current.get('enabled', False)) if current else False
    admin_store.set_maintenance(enabled, message, admin['display_name'])
    return jsonify({'ok': True, 'active': enabled})


# ── Item 16: Gateway backups ──────────────────────────────────────────────

@app.route('/admin/gateway/backup/create', methods=['POST'])
def admin_gateway_backup_create():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    cfg = pgw.load_config()
    admin_store.save_gateway_backup(cfg, admin['display_name'])
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Backup manual do gateway criado', None, _get_client_ip())
    session['_debug_msg'] = 'Backup criado com sucesso!'
    return redirect('/payment/admin')


@app.route('/admin/gateway/backup/delete', methods=['POST'])
def admin_gateway_backup_delete():
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'ok': False, 'error': 'Acesso negado'}), 403
    backup_id = int(request.form.get('backup_id', 0))
    if not backup_id:
        return jsonify({'ok': False, 'error': 'ID inválido'}), 400
    admin_store.delete_gateway_backup(backup_id)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Backup do gateway excluído', f'Backup ID {backup_id}', ip=_get_client_ip())
    return jsonify({'ok': True})


@app.route('/admin/gateway/backup/restore', methods=['POST'])
def admin_gateway_restore():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    backup_id = int(request.form.get('backup_id', 0))
    reason    = request.form.get('reason', '').strip()
    if not reason:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Motivo é obrigatório.'}
        return redirect('/admin/')
    backups = admin_store.get_gateway_backups(limit=20)
    bk = next((b for b in backups if b['id'] == backup_id), None)
    if not bk:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Backup não encontrado.'}
        return redirect('/admin/')
    import json as _json
    cfg = _json.loads(bk['config'])
    pgw.save_config(cfg)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Gateway restaurado de backup',
                      f'Backup ID {backup_id} | Motivo: {reason}', ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': 'Gateway restaurado com sucesso.'}
    return redirect('/payment/admin')


# ── Item 13: Notifications ────────────────────────────────────────────────

@app.route('/admin/notifications')
def admin_notifications():
    admin = g.admin
    notifications = admin_store.get_notifications(
        limit=50, viewer_role=admin['role'], admin_id=admin['id'],
        manage_gateways=admin_store.has_admin_permission(admin, 'manageGateways')
    )
    # Mark all as read
    for n in notifications:
        admin_store.mark_notification_read(n['id'], admin['id'])
    return render_template('admin/notifications.html', admin=admin,
                           notifications=notifications,
                           role_meta=admin_store.ROLE_META)


@app.route('/admin/notifications/mark-all-read', methods=['POST'])
def admin_notifications_mark_all_read():
    admin = g.admin
    admin_store.mark_all_notifications_read(admin['id'], admin['role'])
    return jsonify({'ok': True})


@app.route('/admin/notifications/clear', methods=['POST'])
def admin_notifications_clear():
    admin = g.admin
    admin_store.clear_notifications(admin['id'], admin['role'])
    return jsonify({'ok': True})


@app.route('/admin/notifications/clear-all', methods=['POST'])
def admin_notifications_clear_all():
    """Alias for /admin/notifications/clear used by the 'Limpar histórico'
    button on the full notifications page. Clearing is per-admin (marks
    cleared_by) and never deletes rows for other admins."""
    admin = g.admin
    admin_store.clear_notifications(admin['id'], admin['role'])
    return jsonify({'ok': True})


@app.route('/admin/notifications/api')
def admin_notifications_api():
    admin = g.admin
    manage_gateways = admin_store.has_admin_permission(admin, 'manageGateways')
    count = admin_store.count_unread_notifications(admin['id'], admin['role'], manage_gateways)
    notifications = admin_store.get_notifications(
        limit=30, viewer_role=admin['role'],
        admin_id=admin['id'], unread_only=False, manage_gateways=manage_gateways
    )
    return jsonify({
        'unread': count,
        'notifications': [
            {
                'id':      n['id'],
                'type':    n['type'],
                'message': n['message'],
                'ts':      admin_store.fmt_ts(n['ts']),
                'link':    _notif_link(n['type'], n['message']),
            }
            for n in notifications
        ]
    })


# ── Unified security panel (item 8, V3): Ataques Suspeitos + Forense ────────
# Consolida /admin/suspicious e /admin/forensics em um único painel,
# visível exclusivamente para o cargo OWNER.

_NO_UA_LABEL = '[Filtro Automatizado / Sem Navegador / Script Probing Tool]'


def _parse_os(ua_str: str) -> str:
    """Minimal OS extraction from a User-Agent string."""
    if not ua_str:
        return _NO_UA_LABEL
    ua = ua_str.lower()
    if 'windows nt 10' in ua or 'windows nt 6.4' in ua: return 'Windows 10/11'
    if 'windows nt 6.3' in ua: return 'Windows 8.1'
    if 'windows nt 6.2' in ua: return 'Windows 8'
    if 'windows nt 6.1' in ua: return 'Windows 7'
    if 'windows' in ua: return 'Windows'
    if 'android' in ua: return 'Android'
    if 'iphone' in ua: return 'iPhone (iOS)'
    if 'ipad' in ua: return 'iPad (iOS)'
    if 'mac os x' in ua: return 'macOS'
    if 'linux' in ua: return 'Linux'
    return 'Desconhecido'


def _parse_browser(ua_str: str) -> str:
    """Minimal, order-sensitive Browser extraction from a User-Agent string.
    Order matters: Edge/Opera/Brave/Chrome all include 'chrome' in their UA,
    and Chrome/Safari mobile UAs include 'safari', so the more specific
    tokens must be checked first."""
    if not ua_str:
        return _NO_UA_LABEL
    ua = ua_str.lower()
    if 'edg/' in ua or 'edga/' in ua or 'edgios/' in ua: return 'Microsoft Edge'
    if 'opr/' in ua or 'opera' in ua: return 'Opera'
    if 'brave' in ua: return 'Brave Browser'
    if 'firefox' in ua or 'fxios' in ua: return 'Firefox'
    if 'chrome' in ua or 'crios' in ua: return 'Chrome'
    if 'safari' in ua: return 'Safari'
    return 'Desconhecido'


def _parse_hardware_fields(hw_json_str: str) -> dict:
    """Extract friendly RAM/GPU/cores/resolution strings from the raw
    hardware-fingerprint JSON captured on locked login screens."""
    import json as _json
    try:
        hw = _json.loads(hw_json_str or '{}')
    except Exception:
        hw = {}
    memory = str(hw.get('memory', '') or '')
    cores = str(hw.get('cores', '') or '')
    return {
        'hardware_ram':      f'{memory} RAM' if memory and memory != '?' else '',
        'hardware_gpu':      hw.get('gpu', '') if hw.get('gpu', '?') != '?' else '',
        'hardware_cores':    f'{cores} núcleos' if cores and cores != '?' else '',
        'screen_resolution': hw.get('screen', '') if hw.get('screen', '?') != '?' else '',
    }


@app.route('/admin/security')
def admin_security_view():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    import json as _json
    from collections import defaultdict as _defaultdict
    requests_raw = admin_store.get_suspicious_requests(limit=500)
    suspicious_today = admin_store.count_suspicious_today()

    # ── Group suspicious requests by IP for the accordion view ──────────────
    groups_map: dict = _defaultdict(list)
    for r in requests_raw:
        groups_map[r['ip']].append(r)

    grouped_requests = []
    for ip, reqs in groups_map.items():
        reqs.sort(key=lambda x: x.get('ts', ''), reverse=True)
        total_count  = sum(r.get('count', 1) for r in reqs)
        attack_types = list({r['attack_type'] for r in reqs})
        latest_ts    = max(r.get('ts', '') for r in reqs)
        linked_ips   = admin_store.get_linked_ips_for_ip(ip)
        grouped_requests.append({
            'ip':               ip,
            'requests':         reqs,
            'total_count':      total_count,
            'attack_types':     attack_types,
            'latest_ts':        latest_ts,
            'linked_ips':       linked_ips,
            'browser_name':     reqs[0].get('browser_name') or '—',
            'operating_system': reqs[0].get('operating_system') or '—',
        })
    grouped_requests.sort(key=lambda g: g['latest_ts'], reverse=True)
    requests_total = len(requests_raw)
    # ────────────────────────────────────────────────────────────────────────

    incidents = admin_store.get_brute_force_incidents(limit=200)
    for inc in incidents:
        try:
            inc['hardware'] = _json.loads(inc['hardware_json'] or '{}')
        except Exception:
            inc['hardware'] = {}
        inc['os_name'] = _parse_os(inc.get('ua', ''))
    forensics_today = admin_store.count_brute_force_today()
    unique_ips  = len({i['ip'] for i in incidents})
    proxy_count = sum(1 for i in incidents if i.get('geo_is_proxy'))
    waf_bans    = admin_store.get_waf_bans(limit=200)
    flash_msg   = session.pop('_admin_flash', None)
    return render_template(
        'admin/security.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        requests_list=requests_raw,        # kept for stat counter backward-compat
        requests_total=requests_total,
        grouped_requests=grouped_requests,
        suspicious_today=suspicious_today,
        incidents=incidents,
        forensics_today=forensics_today,
        unique_ips=unique_ips,
        proxy_count=proxy_count,
        waf_bans=waf_bans,
        flash_msg=flash_msg,
        fmt_ts=admin_store.fmt_ts,
    )


@app.route('/admin/api/ip-tooltip/<ip>')
def admin_api_ip_tooltip(ip: str):
    """AJAX — returns geo + proxy metadata + WAF status for the IP hover tooltip.
    Accessible to any logged-in admin (auth guard runs via before_request on /admin/*).
    """
    import re as _re
    if not _re.match(r'^[\d\.:a-fA-F]+$', ip):
        return jsonify({'error': 'invalid'}), 400
    geo        = _get_geo(ip)
    waf_status = admin_store.get_waf_status(ip)
    return jsonify({
        'city':       geo.get('city') or '?',
        'region':     geo.get('region') or '?',
        'country':    geo.get('country') or '?',
        'flag':       geo.get('flag') or '🌐',
        'isp':        geo.get('isp') or geo.get('org') or '—',
        'is_proxy':   bool(geo.get('is_proxy')),
        'is_hosting': bool(geo.get('is_hosting')),
        'waf_status': waf_status['waf_status'],   # 'banned' | 'warning' | 'clean'
        'waf_count':  waf_status['waf_count'],     # 0 | 1 | 2
    })


@app.route('/admin/waf/unban', methods=['POST'])
def admin_waf_unban():
    """Owner-only: lift a WAF ban by IP."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    ip = request.form.get('ip', '').strip()
    if ip:
        admin_store.unban_ip_waf(ip)
        admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                          f'WAF ban removido para IP {ip}', None, ip=_get_client_ip())
        session['_admin_flash'] = {'type': 'success', 'text': f'Ban WAF removido para {ip}.'}
    return redirect('/admin/security')


# ═══ FIREWALL / LISTA DE ACESSO (ADD02 / ADD03) ══════════════════════════

@app.route('/admin/firewall')
def admin_firewall_view():
    """Owner-only: advanced firewall & access-list management page."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    waf_bans      = admin_store.get_waf_bans(limit=500)
    whitelist     = admin_store.get_whitelist()
    flash_msg     = session.pop('_admin_flash', None)
    no_history    = session.pop('_firewall_no_history', None)
    return render_template(
        'admin/firewall.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        waf_bans=waf_bans,
        whitelist=whitelist,
        flash_msg=flash_msg,
        no_history_ips=no_history or [],
        fmt_ts=admin_store.fmt_ts,
    )


@app.route('/admin/firewall/ban', methods=['POST'])
def admin_firewall_ban():
    """Owner-only: manually ban an IP with a dropdown reason."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    import re as _re
    ip     = request.form.get('ip', '').strip()
    reason = request.form.get('reason', '').strip()
    custom = request.form.get('custom_reason', '').strip()
    if reason == '__custom__':
        reason = custom or 'Outro (motivo não especificado)'
    if not ip or not _re.match(r'^[\d\.:a-fA-F]+$', ip):
        session['_admin_flash'] = {'type': 'danger', 'text': 'IP inválido.'}
        return redirect('/admin/firewall')
    if not reason:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Selecione um motivo.'}
        return redirect('/admin/firewall')
    admin_store.ban_ip_waf(ip, reason, '/admin/firewall (manual)', 'MANUAL', '')
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      f'IP {ip} banido manualmente via Firewall — motivo: {reason}',
                      None, ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': f'IP {ip} banido com sucesso.'}
    return redirect('/admin/firewall')


@app.route('/admin/firewall/unban', methods=['POST'])
def admin_firewall_unban():
    """Owner-only: unban an IP from the firewall page."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    ip = request.form.get('ip', '').strip()
    if ip:
        admin_store.unban_ip_waf(ip)
        admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                          f'WAF ban removido para IP {ip} via Firewall', None,
                          ip=_get_client_ip())
        session['_admin_flash'] = {'type': 'success', 'text': f'IP {ip} desbanido.'}
    return redirect('/admin/firewall')


@app.route('/admin/firewall/whitelist/add', methods=['POST'])
def admin_firewall_whitelist_add():
    """Owner-only: add a developer/pentester to the whitelist."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    nome         = request.form.get('nome_dev', '').strip()
    ip_wl        = request.form.get('ip_wl', '').strip()
    fp_wl        = request.form.get('dispositivo_id', '').strip()
    modo_teste   = request.form.get('modo_teste_ativo') == '1'
    if not nome:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Nome obrigatório.'}
        return redirect('/admin/firewall')
    admin_store.add_to_whitelist(nome, ip_wl, fp_wl, modo_teste)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      f'Dev/Pentester "{nome}" adicionado à lista branca (modo_teste={modo_teste})',
                      None, ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': f'"{nome}" adicionado à lista branca.'}
    return redirect('/admin/firewall')


@app.route('/admin/firewall/whitelist/remove', methods=['POST'])
def admin_firewall_whitelist_remove():
    """Owner-only: remove a developer/pentester from the whitelist."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    try:
        entry_id = int(request.form.get('entry_id', 0))
    except (ValueError, TypeError):
        entry_id = 0
    nome = request.form.get('nome_dev', '')
    if entry_id:
        admin_store.remove_from_whitelist(entry_id)
        admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                          f'Dev/Pentester "{nome}" removido da lista branca (ID {entry_id})',
                          None, ip=_get_client_ip())
        session['_admin_flash'] = {'type': 'success', 'text': f'"{nome}" removido da lista branca.'}
    return redirect('/admin/firewall')


@app.route('/admin/firewall/sync', methods=['POST'])
def admin_firewall_sync():
    """Owner-only: auto-import attack dossiers into waf_bans (blacklist)."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    result = admin_store.sync_waf_bans_from_history()
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      f"Sincronização de dossiês: {result['imported']} importados, "
                      f"{result['skipped']} já existiam",
                      None, ip=_get_client_ip())
    msg = (f"Sincronizado: {result['imported']} IPs importados, "
           f"{result['skipped']} já estavam na lista negra.")
    session['_admin_flash'] = {'type': 'success', 'text': msg}
    # If some IPs had no history, return them so the UI can show the modal
    no_hist = result.get('no_history', [])
    if no_hist:
        session['_firewall_no_history'] = no_hist[:50]
    return redirect('/admin/firewall')


@app.route('/debug-security-screen')
def debug_security_screen():
    """Preview the WAF block screen with fake data.
    Only accessible when app.debug is True OR by a logged-in admin.
    """
    admin_ok = False
    try:
        a, _ = _get_admin()
        admin_ok = bool(a)
    except Exception:
        pass
    if not app.debug and not admin_ok:
        abort(404)
    return _render_waf_block(
        ip='203.0.113.42',
        reason='Credential Exfiltration (.env)',
        ts='2026-08-03T19:45:00',
        url_path='/.env.production',
    )


@app.route('/admin/suspicious')
def admin_suspicious_view():
    return redirect('/admin/security')


@app.route('/admin/forensics')
def admin_forensics_view():
    return redirect('/admin/security')


@app.route('/admin/suspicious/delete-by-ip', methods=['POST'])
def admin_suspicious_delete_by_ip():
    """Owner-only: delete ALL suspicious_requests records for a specific IP."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'ok': False, 'error': 'forbidden'}), 403
    ip = request.json.get('ip', '').strip() if request.is_json else request.form.get('ip', '').strip()
    import re as _re
    if not ip or not _re.match(r'^[\d\.:a-fA-F]+$', ip):
        return jsonify({'ok': False, 'error': 'invalid ip'}), 400
    deleted = admin_store.delete_suspicious_by_ip(ip)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      f'Registros suspeitos do IP {ip} apagados ({deleted} linhas)', None,
                      ip=_get_client_ip())
    return jsonify({'ok': True, 'deleted': deleted})


@app.route('/admin/suspicious/clear', methods=['POST'])
def admin_suspicious_clear():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    admin_store.clear_suspicious_requests()
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Registros suspeitos apagados', None, ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': 'Registros de ataques suspeitos apagados.'}
    return redirect('/admin/security')


def _build_dossier_data(ip: str):
    """Gather all dossier data for a given IP — shared by CSV and HTML exports."""
    from datetime import datetime as _dt
    data       = admin_store.get_all_events_by_ip(ip)
    now_str    = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    total_events = (len(data['suspicious']) + len(data['brute_force']) +
                    len(data['audit']))
    # Enrich brute-force rows with parsed OS/browser where missing
    for r in data['brute_force']:
        if not r.get('operating_system'):
            r['operating_system'] = _parse_os(r.get('ua', ''))
        if not r.get('browser_name'):
            r['browser_name'] = _parse_browser(r.get('ua', ''))
    return data, now_str, total_events


@app.route('/admin/forensics/export-dossier/<path:attacker_ip>')
def admin_export_dossier(attacker_ip):
    """Owner-only: generate and download a forensic CSV dossier for a specific attacker IP."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)

    import csv, io
    from datetime import datetime as _dt

    ip = attacker_ip.strip()
    data, now_str, total_events = _build_dossier_data(ip)

    buf = io.StringIO()
    # UTF-8 BOM for Windows Excel compatibility
    buf.write('\ufeff')
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)

    # ── Capa do Dossiê ─────────────────────────────────────────────────────
    w.writerow(['DOSSIE DE EVIDENCIAS FORENSES DE ATAQUES CIBERNETICOS - INFRAESTRUTURA'])
    w.writerow(['Documento:', 'CONFIDENCIAL - Uso exclusivo para autoridades competentes'])
    w.writerow(['Data de Geracao (UTC):', now_str])
    w.writerow(['IP Investigado:', ip])
    w.writerow(['Total de Eventos Registrados:', total_events])
    w.writerow(['Gerado por:', f"{admin['display_name']} ({admin['role']})"])
    w.writerow([])

    # ── IPs Vinculados (mesmo hardware) ────────────────────────────────────
    w.writerow(['=== IPS VINCULADOS (MESMO HARDWARE / DISPOSITIVO FISICO) ==='])
    if data.get('linked_ips'):
        w.writerow(['IP Alternativo', 'Pais', 'Cidade', 'ISP', 'VPN/Proxy', 'SO', 'Navegador',
                    'Primeira Ocorrencia', 'Ultima Ocorrencia', 'Canvas Fingerprint'])
        for r in data['linked_ips']:
            w.writerow([
                r.get('ip', ''),
                r.get('geo_country', ''),
                r.get('geo_city', ''),
                r.get('geo_isp', ''),
                'Sim' if r.get('geo_is_proxy') else 'Nao',
                r.get('operating_system', ''),
                r.get('browser_name', ''),
                r.get('first_seen', ''),
                r.get('last_seen', ''),
                r.get('canvas_fp', ''),
            ])
    else:
        w.writerow(['(nenhum IP alternativo detectado para o mesmo hardware)'])
    w.writerow([])

    # ── Secao 1: Ataques Suspeitos na URL ──────────────────────────────────
    w.writerow(['=== SECAO 1: ATAQUES SUSPEITOS NA URL ==='])
    if data['suspicious']:
        w.writerow(['Timestamp', 'IP', 'SO', 'Navegador', 'Tipo de Ataque', 'Metodo HTTP',
                    'Payload URL Malicioso (Completo)', 'Ocorrencias'])
        for r in data['suspicious']:
            w.writerow([
                r.get('ts', ''), ip,
                r.get('operating_system') or _NO_UA_LABEL,
                r.get('browser_name') or _NO_UA_LABEL,
                r.get('attack_type', ''), r.get('method', ''),
                r.get('url_path', ''), r.get('count', 1),
            ])
    else:
        w.writerow(['(nenhum registro de ataque suspeito encontrado para este IP)'])
    w.writerow([])

    # ── Secao 2: Incidentes de Forca Bruta ────────────────────────────────
    w.writerow(['=== SECAO 2: INCIDENTES DE FORCA BRUTA / INVASAO ==='])
    if data['brute_force']:
        w.writerow([
            'Timestamp', 'IP', 'SO', 'Navegador', 'GPU', 'RAM', 'Nucleos', 'Resolucao',
            'Usuario Tentado', 'Tentativas', 'Pais', 'Estado',
            'Cidade', 'Lat', 'Lon', 'ISP', 'ASN', 'VPN/Proxy', 'Datacenter',
            'Fingerprint Canvas (GPU Hash)', 'IP Interno (WebRTC Leak)', 'User-Agent',
        ])
        for r in data['brute_force']:
            w.writerow([
                r.get('ts', ''), ip,
                r.get('operating_system') or _NO_UA_LABEL,
                r.get('browser_name') or _NO_UA_LABEL,
                r.get('hardware_gpu', ''), r.get('hardware_ram', ''),
                r.get('hardware_cores', ''), r.get('screen_resolution', ''),
                r.get('username_tried', ''), r.get('failures', 0),
                r.get('geo_country', ''), r.get('geo_region', ''),
                r.get('geo_city', ''), r.get('geo_lat', ''), r.get('geo_lon', ''),
                r.get('geo_isp', ''), r.get('geo_asn', ''),
                'Sim' if r.get('geo_is_proxy') else 'Nao',
                'Sim' if r.get('geo_is_hosting') else 'Nao',
                r.get('canvas_fp', ''), r.get('webrtc_ip', ''), r.get('ua', ''),
            ])
    else:
        w.writerow(['(nenhum incidente de forca bruta encontrado para este IP)'])
    w.writerow([])

    # ── Secao 3: Log de Auditoria ──────────────────────────────────────────
    w.writerow(['=== SECAO 3: ENTRADAS DE LOG DE AUDITORIA DO SISTEMA ==='])
    if data['audit']:
        w.writerow(['Timestamp', 'Administrador', 'Cargo', 'Acao', 'Detalhes'])
        for r in data['audit']:
            w.writerow([
                r.get('ts', ''), r.get('admin_name', ''),
                r.get('role', ''), r.get('action', ''), r.get('detail', ''),
            ])
    else:
        w.writerow(['(nenhuma entrada de auditoria encontrada para este IP)'])
    w.writerow([])
    w.writerow(['=== FIM DO DOSSIE FORENSICO ==='])

    admin_store.audit(
        admin['id'], admin['display_name'], admin['role'],
        'dossier_export_csv', f'Dossiê CSV exportado para IP {ip} ({total_events} eventos)',
        ip=_get_client_ip()
    )

    safe_ip  = ip.replace('.', '_').replace(':', '_')
    filename = f'dossie_forensico_{safe_ip}_{_dt.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'

    from flask import make_response as _make_response
    resp = _make_response(buf.getvalue())
    resp.headers['Content-Type']        = 'text/csv; charset=utf-8-sig'
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.headers['Cache-Control']       = 'no-store'
    return resp


@app.route('/admin/forensics/export-dossier-html/<path:attacker_ip>')
def admin_export_dossier_html(attacker_ip):
    """Owner-only: generate and download a professional HTML forensic dossier."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)

    from datetime import datetime as _dt

    ip = attacker_ip.strip()
    data, now_str, total_events = _build_dossier_data(ip)

    # First brute-force incident holds the primary geo + hardware fingerprint
    primary = data['brute_force'][0] if data['brute_force'] else {}

    admin_store.audit(
        admin['id'], admin['display_name'], admin['role'],
        'dossier_export_html', f'Dossiê HTML exportado para IP {ip} ({total_events} eventos)',
        ip=_get_client_ip()
    )

    safe_ip  = ip.replace('.', '_').replace(':', '_')
    filename = f'dossie_forensico_{safe_ip}_{_dt.utcnow().strftime("%Y%m%d_%H%M%S")}.html'

    html = render_template(
        'admin/dossier_report.html',
        ip=ip,
        now_str=now_str,
        total_events=total_events,
        generated_by=f"{admin['display_name']} ({admin['role']})",
        data=data,
        primary=primary,
        no_ua=_NO_UA_LABEL,
    )

    from flask import make_response as _make_response
    resp = _make_response(html)
    resp.headers['Content-Type']        = 'text/html; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.headers['Cache-Control']       = 'no-store'
    return resp


@app.route('/admin/forensics/clear', methods=['POST'])
def admin_forensics_clear():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    with admin_store._lock, admin_store._conn() as c:
        c.execute("DELETE FROM brute_force_incidents")
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Registros forenses apagados', None, ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': 'Registros forenses apagados.'}
    return redirect('/admin/security')


# ── Geo lookup for active sessions ────────────────────────────────────────

@app.route('/admin/geo/<path:ip_addr>')
def admin_geo_lookup(ip_addr):
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'manage_admins'):
        abort(404)
    # Sanitize: only allow valid IP characters
    import re as _re
    if not _re.match(r'^[\d.:a-fA-F]+$', ip_addr):
        return jsonify({'error': 'IP inválido'}), 400
    gd = _get_geo(ip_addr)
    return jsonify({
        'city':         gd.get('city', '?'),
        'region':       gd.get('region', '?'),
        'country':      gd.get('country', '?'),
        'country_code': gd.get('country_code', ''),
        'flag':         gd.get('flag', '🌐'),
        'org':          gd.get('org', ''),
    })


# ── Item 17: Reports ──────────────────────────────────────────────────────

@app.route('/admin/reports', methods=['GET', 'POST'])
def admin_reports():
    admin = g.admin
    if request.method == 'POST':
        message   = request.form.get('message', '').strip()[:1000]
        if admin['role'] == 'owner':
            session['_admin_flash'] = {'type': 'info', 'text': 'Owner não precisa enviar relatórios.'}
            return redirect('/admin/reports')
        if not message:
            session['_admin_flash'] = {'type': 'danger', 'text': 'Mensagem não pode estar vazia.'}
            return redirect('/admin/reports')
        # Helpers can choose target; moderators → admin; admins → owner
        if admin['role'] == 'helper':
            to_role = request.form.get('to_role', 'moderator')
            if to_role not in ('moderator', 'admin'):
                to_role = 'moderator'
        else:
            to_map  = {'moderator': 'admin', 'admin': 'owner'}
            to_role = to_map.get(admin['role'], 'owner')
        admin_store.create_report(admin['id'], admin['display_name'],
                                  admin['role'], to_role, message)
        admin_store.add_notification(
            'report',
            f'Novo relatório de {admin["display_name"]} ({admin_store.ROLE_META[admin["role"]]["label"]})',
            min_role=to_role
        )
        session['_admin_flash'] = {'type': 'success', 'text': 'Relatório enviado com sucesso.'}
        return redirect('/admin/reports')

    reports   = admin_store.get_reports(admin['role'], admin['id'])
    flash_msg = session.pop('_admin_flash', None)
    return render_template('admin/reports.html', admin=admin, reports=reports,
                           role_meta=admin_store.ROLE_META, flash_msg=flash_msg)


@app.route('/admin/reports/resolve', methods=['POST'])
def admin_report_resolve():
    admin     = g.admin
    report_id = int(request.form.get('report_id', 0))
    report    = admin_store.get_report_by_id(report_id)
    if not report:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Relatório não encontrado.'}
        return redirect('/admin/reports')
    if report['from_id'] == admin['id']:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Você não pode resolver seu próprio relatório.'}
        return redirect('/admin/reports')
    # Only the recipient role (or owner) can resolve
    resolver_level = admin_store.ROLE_LEVEL.get(admin['role'], 99)
    target_level   = admin_store.ROLE_LEVEL.get(report['to_role'], 99)
    if admin['role'] != 'owner' and resolver_level != target_level:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Apenas o destinatário do relatório pode resolvê-lo.'}
        return redirect('/admin/reports')
    admin_store.resolve_report(report_id, admin['display_name'])
    # Notify the reporter that their report was resolved (visible to their role + above)
    reporter_role = report['from_role']
    admin_store.add_notification(
        'report',
        f'✅ Relatório de {report["from_name"]} ({admin_store.ROLE_META[reporter_role]["label"]}) foi resolvido por {admin["display_name"]}',
        min_role=reporter_role
    )
    session['_admin_flash'] = {'type': 'success', 'text': 'Relatório marcado como resolvido.'}
    return redirect('/admin/reports')


@app.route('/admin/reports/delete', methods=['POST'])
def admin_report_delete():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(403)
    report_id = int(request.form.get('report_id', 0))
    admin_store.delete_report(report_id)
    session['_admin_flash'] = {'type': 'success', 'text': 'Relatório excluído.'}
    return redirect('/admin/reports')


# ── Team chat (floating widget) ───────────────────────────────────────────

@app.route('/admin/chat/send-api', methods=['POST'])
def admin_chat_send_api():
    """Send a chat message via JSON (for floating widget)."""
    admin   = g.admin
    data    = request.get_json(silent=True) or {}
    message = str(data.get('message', '')).strip()[:500]
    channel = str(data.get('channel', 'team'))
    if not message:
        return jsonify({'error': 'Mensagem vazia'}), 400
    if channel == 'internal':
        if admin['role'] == 'helper' and not admin_store.get_internal_access_helper():
            return jsonify({'error': 'Sem permissão'}), 403
        if admin['role'] == 'moderator' and not admin_store.get_internal_access_moderator():
            return jsonify({'error': 'Sem permissão'}), 403
    admin_store.send_chat(admin['id'], admin['display_name'],
                          admin['role'], admin['avatar'], message, channel)
    return jsonify({'ok': True})


@app.route('/admin/chat/api')
def admin_chat_api():
    """Poll new messages since a given ID (for floating widget)."""
    admin    = g.admin
    since_id = int(request.args.get('since', 0))
    channel  = request.args.get('channel', 'team')
    empty = {'messages': [], 'typing': [], 'others_seen_up_to': 0}
    if channel == 'internal':
        if admin['role'] == 'helper' and not admin_store.get_internal_access_helper():
            return jsonify(empty)
        if admin['role'] == 'moderator' and not admin_store.get_internal_access_moderator():
            return jsonify(empty)
    messages = admin_store.get_chat_messages(limit=50, since_id=since_id, channel=channel)
    if messages:
        admin_store.update_last_seen(channel, admin['id'], max(m['id'] for m in messages))
    elif since_id > 0:
        admin_store.update_last_seen(channel, admin['id'], since_id)
    crowned_id = admin_store.get_crowned_admin_id()
    return jsonify({
        'messages': [{
            'id':      m['id'],
            'name':    m['admin_name'],
            'role':    m['role'],
            'avatar':  m['avatar'],
            'msg':     m['message'],
            'ts':      admin_store.fmt_ts(m['ts']),
            'mine':    m['admin_id'] == admin['id'],
            'crowned': m['admin_id'] == crowned_id,
        } for m in messages],
        'typing':            admin_store.get_typing(channel, admin['id']),
        'others_seen_up_to': admin_store.get_others_seen_up_to(channel, admin['id']),
    })


@app.route('/admin/chat/rooms-api')
def admin_chat_rooms_api():
    """JSON list of private rooms for the floating widget."""
    admin = g.admin
    if admin['role'] == 'owner':
        rooms = admin_store.get_all_rooms()
    else:
        all_rooms = admin_store.get_rooms_for_admin(admin['id'])
        rooms = [r for r in all_rooms if not r.get('closed')]
    result = []
    for r in rooms:
        last = admin_store.get_room_last_message(r['id'])
        is_participant = admin_store.is_room_participant(r['id'], admin['id'])
        other_id = r['p2_id'] if r['p1_id'] == admin['id'] else r['p1_id']
        other = admin_store.get_admin_by_id(other_id)
        result.append({
            'id':             r['id'],
            'title':          f"{r['p1_name']} ↔ {r['p2_name']}",
            'other_username': other['username'] if other else '',
            'last_msg':       last['message'][:60] if last else '',
            'last_ts':        admin_store.fmt_ts(last['ts']) if last else '',
            'closed':         bool(r['closed']),
            'is_participant': is_participant,
        })
    return jsonify(result)


@app.route('/admin/chat/room/create-api', methods=['POST'])
def admin_chat_room_create_api():
    """Create a private room from the floating widget (JSON)."""
    admin     = g.admin
    data      = request.get_json(silent=True) or {}
    target_id = int(data.get('target_id', 0))
    target    = admin_store.get_admin_by_id(target_id)
    if not target or not target.get('active'):
        return jsonify({'error': 'Usuário não encontrado'}), 404
    if target['id'] == admin['id']:
        return jsonify({'error': 'Não pode conversar consigo mesmo'}), 400
    room_id = admin_store.create_room(admin['id'], admin['display_name'],
                                      target['id'], target['display_name'])
    return jsonify({'ok': True, 'room_id': room_id,
                    'title': f"{admin['display_name']} ↔ {target['display_name']}"})


@app.route('/admin/chat/internal-access', methods=['POST'])
def admin_chat_internal_access():
    """Owner-only: toggle moderator access to the internal channel."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    data    = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled', True))
    admin_store.set_internal_access_moderator(enabled)
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           f"{'Ativou' if enabled else 'Desativou'} acesso de Moderadores ao chat Interno",
                           None, request.remote_addr)
    return jsonify({'ok': True, 'enabled': enabled})


@app.route('/admin/chat/helper-access', methods=['POST'])
def admin_chat_helper_access():
    """Owner-only: toggle helper access to the internal channel."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    data    = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled', False))
    admin_store.set_internal_access_helper(enabled)
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           f"{'Ativou' if enabled else 'Desativou'} acesso de Ajudantes ao chat Interno",
                           None, request.remote_addr)
    return jsonify({'ok': True, 'enabled': enabled})


@app.route('/admin/chat/clear-team', methods=['POST'])
def admin_chat_clear_team():
    """Owner-only: clear all messages from the team channel."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    admin_store.clear_channel_messages('team')
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Limpou o chat da Equipe', None, request.remote_addr)
    return jsonify({'ok': True})


@app.route('/admin/chat/clear-internal', methods=['POST'])
def admin_chat_clear_internal():
    """Owner-only: clear all messages from the internal channel."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    admin_store.clear_channel_messages('internal')
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Limpou o chat Interno', None, request.remote_addr)
    return jsonify({'ok': True})


@app.route('/admin/chat/room/<int:room_id>/message/<int:msg_id>/delete', methods=['POST'])
def admin_chat_room_message_delete(room_id, msg_id):
    """Delete a single message from a private room.
    Owners may delete any message; participants can only delete their own."""
    admin = g.admin
    room = admin_store.get_room(room_id)
    if not room:
        return jsonify({'error': 'Sala não encontrada'}), 404
    is_owner = admin['role'] == 'owner'
    is_participant = admin_store.is_room_participant(room_id, admin['id'])
    if not is_participant and not is_owner:
        return jsonify({'error': 'Sem permissão'}), 403
    ok = admin_store.delete_room_message(room_id, msg_id, admin['id'], is_owner)
    if not ok:
        return jsonify({'error': 'Mensagem não encontrada ou sem permissão'}), 404
    return jsonify({'ok': True})


@app.route('/admin/chat/room/<int:room_id>/delete', methods=['POST'])
def admin_chat_room_delete(room_id):
    """Owner-only: permanently delete a closed private room and all its messages."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    room = admin_store.get_room(room_id)
    if not room:
        return jsonify({'error': 'Sala não encontrada'}), 404
    if not room.get('closed'):
        return jsonify({'error': 'Só é possível apagar conversas encerradas'}), 400
    admin_store.delete_room(room_id)
    return jsonify({'ok': True})


@app.route('/admin/chat/typing-notify', methods=['POST'])
def admin_chat_typing_notify():
    """Record that the current user is typing in a channel or room."""
    admin = g.admin
    data    = request.get_json(silent=True) or {}
    channel = data.get('channel')
    room_id = data.get('room_id')
    if room_id:
        key = f'room_{int(room_id)}'
    elif channel in ('team', 'internal'):
        key = channel
    else:
        key = 'team'
    admin_store.set_typing(key, admin['id'], admin['display_name'])
    return jsonify({'ok': True})


# ── Private Chat Rooms ─────────────────────────────────────────────────────

@app.route('/admin/chat')
def admin_chat_view():
    admin  = g.admin
    if admin['role'] == 'owner':
        rooms = admin_store.get_all_rooms()
    else:
        rooms = admin_store.get_rooms_for_admin(admin['id'])
    # Attach last message preview to each room
    for r in rooms:
        last = admin_store.get_room_last_message(r['id'])
        r['last_msg'] = last['message'] if last else ''
        r['last_ts']  = admin_store.fmt_ts(last['ts']) if last else ''
    all_admins = [a for a in admin_store.list_admins()
                  if a['id'] != admin['id'] and a['active']]
    return render_template('admin/chat.html', admin=admin,
                           role_meta=admin_store.ROLE_META,
                           rooms=rooms, all_admins=all_admins)


@app.route('/admin/chat/room/create', methods=['POST'])
def admin_chat_room_create():
    admin     = g.admin
    target_id = int(request.form.get('target_id', 0))
    target    = admin_store.get_admin_by_id(target_id)
    if not target or not target.get('active'):
        session['_admin_flash'] = {'type': 'danger', 'text': 'Usuário não encontrado.'}
        return redirect('/admin/chat')
    if target['id'] == admin['id']:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Não pode conversar consigo mesmo.'}
        return redirect('/admin/chat')
    room_id = admin_store.create_room(admin['id'], admin['display_name'],
                                      target['id'], target['display_name'])
    return redirect(f'/admin/chat/room/{room_id}')


@app.route('/admin/chat/room/<int:room_id>')
def admin_chat_room_view(room_id):
    admin = g.admin
    room  = admin_store.get_room(room_id)
    if not room:
        abort(404)
    # Only participants and owner can view
    is_participant = admin_store.is_room_participant(room_id, admin['id'])
    if not is_participant and admin['role'] != 'owner':
        abort(404)
    messages = admin_store.get_room_messages(room_id, limit=200)
    # Determine partner name for non-owner participants
    if is_participant:
        partner_name = room['p2_name'] if room['p1_id'] == admin['id'] else room['p1_name']
    else:
        partner_name = None
    return render_template('admin/chat_room.html', admin=admin,
                           role_meta=admin_store.ROLE_META,
                           room=room, messages=messages,
                           is_participant=is_participant,
                           partner_name=partner_name,
                           crowned_id=admin_store.get_crowned_admin_id())


@app.route('/admin/chat/room/<int:room_id>/send', methods=['POST'])
def admin_chat_room_send(room_id):
    admin = g.admin
    room  = admin_store.get_room(room_id)
    if not room:
        return jsonify({'error': 'Sala não encontrada'}), 404
    if not admin_store.is_room_participant(room_id, admin['id']):
        return jsonify({'error': 'Não autorizado'}), 403
    if room['closed']:
        return jsonify({'error': 'Esta conversa foi encerrada'}), 400
    message = str(request.json.get('message', '')).strip() if request.json else ''
    if not message:
        return jsonify({'error': 'Mensagem vazia'}), 400
    admin_store.send_room_message(room_id, admin['id'], admin['display_name'],
                                  admin['role'], admin['avatar'], message)
    return jsonify({'ok': True})


@app.route('/admin/chat/room/<int:room_id>/api')
def admin_chat_room_api(room_id):
    admin = g.admin
    room  = admin_store.get_room(room_id)
    empty = {'messages': [], 'typing': [], 'others_seen_up_to': 0}
    if not room:
        return jsonify(empty)
    is_participant = admin_store.is_room_participant(room_id, admin['id'])
    if not is_participant and admin['role'] != 'owner':
        return jsonify(empty)
    since_id = int(request.args.get('since', 0))
    messages = admin_store.get_room_messages(room_id, limit=50, since_id=since_id)
    key = f'room_{room_id}'
    if messages:
        admin_store.update_last_seen(key, admin['id'], max(m['id'] for m in messages))
    elif since_id > 0:
        admin_store.update_last_seen(key, admin['id'], since_id)
    return jsonify({
        'messages': [{
            'id':     m['id'],
            'name':   m['admin_name'],
            'role':   m['role'],
            'avatar': m['avatar'],
            'msg':    m['message'],
            'ts':     admin_store.fmt_ts(m['ts']),
            'mine':   m['admin_id'] == admin['id'],
            'system': m['admin_id'] == 0,
        } for m in messages],
        'typing':           admin_store.get_typing(key, admin['id']),
        'others_seen_up_to': admin_store.get_others_seen_up_to(key, admin['id']),
        'recipient_online': admin_store.get_room_other_participant_status(room_id, admin['id']) in ('online', 'busy'),
    })


@app.route('/admin/api/chat/mark-as-read', methods=['POST'])
def api_chat_mark_as_read():
    """Marca as mensagens de uma conversa (sala privada ou canal) como lidas
    pelo usuário atual. Disparado ao abrir/focar a janela de chat.
    Protegido pelo guard central de /admin/* (g.admin sempre definido aqui)."""
    admin   = g.admin
    data    = request.get_json(silent=True) or {}
    room_id = data.get('room_id')
    channel = data.get('channel')
    if room_id is not None:
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'room_id inválido'}), 400
        if not admin_store.is_room_participant(room_id, admin['id']) and admin['role'] != 'owner':
            return jsonify({'ok': False, 'error': 'Sem permissão'}), 403
        key = f'room_{room_id}'
        last = admin_store.get_room_last_message(room_id)
    elif channel in ('team', 'internal'):
        if channel == 'internal':
            if admin['role'] == 'helper' and not admin_store.get_internal_access_helper():
                return jsonify({'ok': False, 'error': 'Sem permissão'}), 403
            if admin['role'] == 'moderator' and not admin_store.get_internal_access_moderator():
                return jsonify({'ok': False, 'error': 'Sem permissão'}), 403
        key = channel
        msgs = admin_store.get_chat_messages(limit=1, channel=channel)
        last = msgs[-1] if msgs else None
    else:
        return jsonify({'ok': False, 'error': 'Parâmetro inválido'}), 400
    if last:
        admin_store.update_last_seen(key, admin['id'], last['id'])
    return jsonify({'ok': True})


@app.route('/admin/chat/room/<int:room_id>/close', methods=['POST'])
def admin_chat_room_close(room_id):
    admin = g.admin
    room  = admin_store.get_room(room_id)
    if not room:
        abort(404)
    if not admin_store.is_room_participant(room_id, admin['id']) and admin['role'] != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    if not room['closed']:
        admin_store.close_room(room_id, admin['display_name'])
    return jsonify({'ok': True})


@app.route('/admin/chat/room/<int:room_id>/transcript')
def admin_chat_room_transcript(room_id):
    admin = g.admin
    if admin['role'] != 'owner':
        abort(403)
    room  = admin_store.get_room(room_id)
    if not room:
        abort(404)
    messages = admin_store.get_room_messages(room_id, limit=5000)
    html = _build_transcript_html(room, messages, admin_store.ROLE_META)
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="transcript_room_{room_id}.html"'
    return resp


def _build_transcript_html(room: dict, messages: list, role_meta: dict) -> str:
    import html as html_lib
    lines = ['<!DOCTYPE html><html lang="pt-BR"><head>',
             '<meta charset="UTF-8">',
             f'<title>Transcript — Sala #{room["id"]}</title>',
             '<style>',
             'body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:0}',
             '.header{background:#161b22;border-bottom:1px solid #30363d;padding:1rem 1.5rem}',
             '.header h1{margin:0;font-size:1.1rem;color:#58a6ff}',
             '.header p{margin:.25rem 0 0;color:#8b949e;font-size:.85rem}',
             '.messages{padding:1.2rem 1.5rem;display:flex;flex-direction:column;gap:.4rem}',
             '.msg{display:flex;gap:.65rem;align-items:flex-start;max-width:75%}',
             '.msg.mine{align-self:flex-end;flex-direction:row-reverse}',
             '.avatar{width:32px;height:32px;border-radius:50%;background:#30363d;display:flex;align-items:center;justify-content:center;font-size:.75rem;flex-shrink:0}',
             '.bubble{padding:.45rem .8rem;border-radius:14px;font-size:.87rem;word-break:break-word}',
             '.bubble.mine-b{background:#1d4ed8;border-bottom-right-radius:4px}',
             '.bubble.other-b{background:#21262d;border:1px solid #30363d;border-bottom-left-radius:4px}',
             '.meta{font-size:.65rem;color:#8b949e;margin-bottom:.1rem}',
             '.system{text-align:center;color:#6b7280;font-size:.75rem;margin:.5rem 0;width:100%;align-self:center}',
             '.footer{border-top:1px solid #30363d;padding:.75rem 1.5rem;color:#8b949e;font-size:.75rem;text-align:center}',
             '</style></head><body>',
             '<div class="header">',
             f'<h1>💬 Conversa privada — Sala #{room["id"]}</h1>',
             f'<p>{html_lib.escape(room["p1_name"])} ↔ {html_lib.escape(room["p2_name"])} · Iniciada em {room["created_at"]}',
             f'{"  · 🔴 Encerrada em " + room["closed_at"] + " por " + html_lib.escape(room["closed_by"] or "") if room["closed"] else ""}</p>',
             '</div>',
             '<div class="messages">']
    for m in messages:
        if m['admin_id'] == 0:
            lines.append(f'<div class="system">{html_lib.escape(m["message"])}</div>')
            continue
        is_p1 = m['admin_id'] == room['p1_id']
        side   = 'mine' if is_p1 else ''
        bside  = 'mine-b' if is_p1 else 'other-b'
        role_l = role_meta.get(m['role'], {}).get('label', m['role'])
        ts     = m['ts']
        lines.append(f'<div class="msg {side}">')
        lines.append(f'<div class="avatar">{html_lib.escape(m["admin_name"][:1])}</div>')
        lines.append(f'<div><div class="meta"><strong>{html_lib.escape(m["admin_name"])}</strong> ({role_l}) · {ts}</div>')
        lines.append(f'<div class="bubble {bside}">{html_lib.escape(m["message"])}</div></div>')
        lines.append('</div>')
    lines.append('</div>')
    lines.append('<div class="footer">Transcript gerado automaticamente — uso interno</div>')
    lines.append('</body></html>')
    return '\n'.join(lines)


# ── Discord Webhooks ─────────────────────────────────────────────────────────

@app.route('/admin/webhooks')
def admin_webhooks():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    webhooks = admin_store.get_webhooks()
    import json as _json
    for wh in webhooks:
        wh['url'] = dwh.normalize_webhook_url(wh.get('url', ''))
        try:
            wh['events_list'] = _json.loads(wh['events'])
        except Exception:
            wh['events_list'] = []
    return render_template('admin/webhooks.html', admin=admin,
                           webhooks=webhooks,
                           all_events=dwh.EVENTS,
                           role_meta=admin_store.ROLE_META)


@app.route('/admin/webhooks/add', methods=['POST'])
def admin_webhooks_add():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    name   = request.form.get('name', '').strip()[:80]
    url    = dwh.normalize_webhook_url(request.form.get('url', '').strip())[:500]
    events = request.form.getlist('events')
    if not name or not url:
        session['_admin_flash'] = {'type': 'danger', 'text': 'Nome e URL são obrigatórios.'}
        return redirect('/admin/webhooks')
    admin_store.add_webhook(name, url, events)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Webhook Discord adicionado', f'{name} — eventos: {", ".join(events)}',
                      ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': f'Webhook "{name}" criado.'}
    return redirect('/admin/webhooks')


@app.route('/admin/webhooks/update', methods=['POST'])
def admin_webhooks_update():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    wh_id   = int(request.form.get('wh_id', 0))
    name    = request.form.get('name', '').strip()[:80]
    url     = dwh.normalize_webhook_url(request.form.get('url', '').strip())[:500]
    enabled = request.form.get('enabled', '0') == '1'
    events  = request.form.getlist('events')
    if not wh_id or not name or not url:
        return jsonify({'ok': False, 'error': 'Dados inválidos'}), 400
    admin_store.update_webhook(wh_id, name, url, enabled, events)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Webhook Discord atualizado', f'ID {wh_id} — {name}',
                      ip=_get_client_ip())
    session['_admin_flash'] = {'type': 'success', 'text': f'Webhook "{name}" atualizado.'}
    return redirect('/admin/webhooks')


@app.route('/admin/webhooks/toggle', methods=['POST'])
def admin_webhooks_toggle():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    wh_id = int(request.form.get('wh_id', 0))
    if not wh_id:
        return jsonify({'ok': False}), 400
    new_state = admin_store.toggle_webhook(wh_id)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Webhook ' + ('ativado' if new_state else 'desativado'),
                      f'ID {wh_id}', ip=_get_client_ip())
    return jsonify({'ok': True, 'enabled': new_state})


@app.route('/admin/webhooks/delete', methods=['POST'])
def admin_webhooks_delete():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    wh_id = int(request.form.get('wh_id', 0))
    if not wh_id:
        return jsonify({'ok': False}), 400
    admin_store.delete_webhook(wh_id)
    admin_store.audit(admin['id'], admin['display_name'], admin['role'],
                      'Webhook Discord excluído', f'ID {wh_id}',
                      ip=_get_client_ip())
    return jsonify({'ok': True})


@app.route('/admin/webhooks/test', methods=['POST'])
def admin_webhooks_test():
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)
    url = dwh.normalize_webhook_url(request.form.get('url', '').strip())
    if not url:
        return jsonify({'ok': False, 'error': 'URL vazia'})
    ok, code = dwh.test_webhook(url)
    return jsonify({'ok': ok, 'code': code})


if __name__ == '__main__':
    app_logger.info("🚀 Iniciando Media Scraper...")
    app_logger.info("🌐 Servidor disponível em: http://localhost:5000")
    _run_main = os.environ.get('WERKZEUG_RUN_MAIN')
    # _run_main == 'true'  → processo filho do reloader (arrancar threads)
    # _run_main is None    → sem reloader ativo (produção) OU 1º boot do watcher
    # Para distinguir produção do watcher: watcher define WERKZEUG_SERVER_FD
    _is_watcher = (_run_main is None and os.environ.get('WERKZEUG_SERVER_FD') is not None)
    if _run_main == 'true' or (not _is_watcher and _run_main is None):
        _ensure_auto_ping()  # starts DB-driven ping daemon thread
        app_logger.info("🏓 Auto-ping DB-driven iniciado (sobrevive a restarts)")
        threading.Thread(target=_pix_expiry_worker, daemon=True).start()
        app_logger.info("⏰ Thread de expiração de PIX ativada")

    app.run(host="0.0.0.0", port=5000, debug=True)