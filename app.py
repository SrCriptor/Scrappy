import io
import os
import re
import logging
import threading
import time
import random
from apscheduler.schedulers.background import BackgroundScheduler
import bs4  # BeautifulSoup
import requests
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
import uuid
import colorlog

__version__ = "2.2.0"

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
oracle_bot.ligar_bot_oracle()

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

def _get_client_ip():
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() if xff else (request.remote_addr or '127.0.0.1')

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
})

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
            if not admin_store.has_perm(admin['role'], perm):
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
        r = requests.get(url, headers=headers, stream=True, timeout=30)
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

    try:
        r = requests.get(url, headers=headers, stream=True, timeout=20)
        r.raise_for_status()

        filename = url.split('/')[-1]

        stats_store.increment('downloads')
        return Response(
            r.iter_content(chunk_size=8192),
            content_type=r.headers.get('Content-Type', 'application/octet-stream'),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
        )
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status not in (404, 410):
            debug_logger.log_failed_url(url, f"HTTP {status}", "http_error")
        return f"Erro ao baixar o arquivo: HTTP {status}", 500
    except requests.exceptions.ConnectionError as e:
        debug_logger.log_failed_url(url, "Erro de conexão", "connection_error")
        return f"Erro de conexão ao tentar baixar: {str(e)}", 500
    except requests.exceptions.Timeout:
        debug_logger.log_failed_url(url, "Timeout", "timeout")
        return "Timeout ao tentar baixar o arquivo.", 500
    except requests.RequestException as e:
        debug_logger.log_failed_url(url, str(e), "request_error")
        return f"Erro ao baixar o arquivo: {str(e)}", 500

      
@app.route('/')
def index():
    """Página principal com formulário para inserir URL"""
    stats_store.increment('views')
    payments_disabled = pgw.are_payments_disabled()
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
    return render_template(
        'index.html',
        payments_disabled=payments_disabled,
        vip_tiers=vip_tiers,
        vip0_links=_tm.get(0, 100),
        vip1_links=_tm.get(1, 60),
        vip2_links=_tm.get(2, 50),
        vip3_links=_tm.get(3, 40),
        vip4_links=_tm.get(4, 30),
        vip5_links=_tm.get(5, 20),
        vip6_links=_tm.get(6, 10),
    )

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
    """Página pública informativa sobre o painel de debug — sem autenticação."""
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
    return render_template('batch_panel.html', admin=admin,
                           role_meta=admin_store.ROLE_META,
                           has_perm=admin_store.has_perm,
                           batch_msg=batch_msg)


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
            'versao': _APP_VERSION,
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

@app.route('/dashboard/config', methods=['GET', 'POST'])
def dashboard_config():
    admin = g.admin
    if not admin_store.has_perm(admin['role'], 'debug'):
        abort(404)
    if request.method == 'POST':
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
    return render_template('dashboard_config.html',
                           overrides=overrides, real=real, cfg_msg=cfg_msg,
                           app_version=_APP_VERSION,
                           sys_status='Seguro',
                           sys_memoria=(f'{mem_mb} MB' if mem_mb is not None else 'N/A'),
                           sys_performance=_get_performance_score())

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

        # Item 3: em fluxos de redirect (cartão), o navegador sai do site e pode
        # não voltar à mesma aba — então o apelido/avatar são coletados AQUI,
        # antes de gerar o pagamento, e ficam salvos junto ao registro pendente.
        # Quando o gateway confirmar o pagamento (webhook), fulfill_pix_payment()
        # usa esses dados para completar o cadastro no ranking automaticamente.
        pending_nickname  = str(data.get('nickname', '')).strip()[:40] or None
        pending_avatar_id = str(data.get('avatar_id', '')).strip().lower() or None
        donor_token        = request.cookies.get('donation_token', '').strip() or None
        if pending_avatar_id and pending_avatar_id not in donor_avatars.get_valid_avatar_ids():
            pending_avatar_id = None

        if method == 'card' and gateway_id == 'stripe':
            base_url = request.host_url.rstrip('/')
            result = gw.create_card_payment(
                amount, 'Doação',
                success_url=f'{base_url}/?payment=success',
                cancel_url=f'{base_url}/?payment=cancel',
            )
        elif method == 'card' and gateway_id == 'paypal':
            base_url = request.host_url.rstrip('/')
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
            return jsonify({'success': False, 'error': 'Não implementado'}), 200

        # Save PIX payment for tracking
        try:
            if result.payment_id:
                admin_store.save_pix_payment(result.payment_id, gateway_id, amount,
                                             nickname=pending_nickname, avatar_id=pending_avatar_id,
                                             donor_token=donor_token)
        except Exception:
            pass
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
        # Notificação financeira — tipo 'payment', visível apenas para Owner
        # (ou Admin quando allow_admin_gateway=True, conforme _notif_visible)
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
    return jsonify({'status': status})

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


@app.route('/payment/admin/disable-all', methods=['POST'])
def payment_admin_disable_all():
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    cfg = pgw.load_config()
    # Save which were active before disabling
    prev_active = [gid for gid, gdata in cfg.get('gateways', {}).items() if gdata.get('enabled')]
    prev_active += [cg['id'] for cg in cfg.get('custom_gateways', []) if cg.get('enabled')]
    cfg['previously_active_ids'] = prev_active
    for gid in cfg.get('gateways', {}):
        cfg['gateways'][gid]['enabled'] = False
    for cg in cfg.get('custom_gateways', []):
        cg['enabled'] = False
    pgw.save_config(cfg)
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Desativar Todos Gateways', None, _get_client_ip())
    admin_store.add_notification(
        'warning',
        f'🚫 Todos os meios de pagamento foram desativados por {admin["display_name"]}',
        min_role='admin'
    )
    return jsonify({'ok': True, 'previously_active': prev_active})


@app.route('/payment/admin/reactivate-all', methods=['POST'])
def payment_admin_reactivate_all():
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    cfg = pgw.load_config()
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
    pgw.save_config(cfg)
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           'Reativar Gateways Anteriores', None, _get_client_ip())
    admin_store.add_notification(
        'payment',
        f'✅ Meios de pagamento reativados por {admin["display_name"]}',
        min_role='owner'
    )
    return jsonify({'ok': True, 'reactivated': prev})


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
    cfg = pgw.load_config_locked()
    # Verifica se o gid é um gateway válido (padrão ou personalizado)
    known = list(pgw._GATEWAY_CLASSES.keys()) + [cg['id'] for cg in cfg.get('custom_gateways', [])]
    if gid not in known:
        return jsonify({'error': 'Gateway desconhecido'}), 400
    cfg['primary_gateway'] = gid
    pgw.save_config(cfg)  # save_config já usa o mesmo Lock internamente
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           f'Alterar Gateway Padrão → {gid}', None, _get_client_ip())
    return jsonify({'ok': True, 'primary_gateway': gid})


@app.route('/payment/admin/toggle-gateway', methods=['POST'])
def payment_admin_toggle_gateway():
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    gid = data.get('gid', '').strip()
    enabled = bool(data.get('enabled', False))
    if not gid:
        return jsonify({'error': 'gid obrigatório'}), 400
    cfg = pgw.load_config()
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
    pgw.save_config(cfg)
    return jsonify({'ok': True, 'gid': gid, 'enabled': enabled})


@app.route('/payment/admin/toggle-stripe-pix', methods=['POST'])
def payment_admin_toggle_stripe_pix():
    """AJAX: salva imediatamente o toggle 'Habilitar Pix via Stripe' (Item 8),
    igual ao padrão real-time já usado para ativar/desativar gateways."""
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled', False))
    cfg = pgw.load_config_locked()
    cfg.setdefault('gateways', {}).setdefault('stripe', {})['stripe_pix_enabled'] = enabled
    pgw.save_config(cfg)
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           f'Pix via Stripe → {"ativado" if enabled else "desativado"}', None, _get_client_ip())
    return jsonify({'ok': True, 'enabled': enabled})


@app.route('/payment/admin/paypal-sandbox-toggle', methods=['POST'])
def payment_admin_paypal_sandbox_toggle():
    """AJAX: salva imediatamente o toggle 'Habilitar Sandbox PayPal' sem recarregar a página."""
    admin, _ = _get_admin()
    if not _has_gateway_access(admin):
        return jsonify({'error': 'Não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    sandbox = bool(data.get('sandbox', True))
    cfg = pgw.load_config_locked()
    cfg.setdefault('gateways', {}).setdefault('paypal', {})['sandbox'] = sandbox
    pgw.save_config(cfg)
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           f'PayPal Sandbox → {"ativado" if sandbox else "desativado (produção)"}', None, _get_client_ip())
    return jsonify({'ok': True, 'sandbox': sandbox})


@app.route('/payment/disabled-status')
def payment_disabled_status():
    return jsonify({'disabled': pgw.are_payments_disabled()})


def _has_gateway_access(admin) -> bool:
    """Gateway access: owner sempre; admin somente se allow_admin_gateway=True."""
    if not admin:
        return False
    if admin['role'] == 'owner':
        return True
    if admin['role'] == 'admin' and admin_store.get_allow_admin_gateway():
        return True
    return False


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
        cfg = pgw.load_config()
        cfg['primary_gateway'] = request.form.get('primary_gateway', 'manual')
        enabled = set(request.form.getlist('enabled_gateways'))

        # ── Gateways padrão ──
        for gid in list(pgw._GATEWAY_CLASSES.keys()):
            if gid not in cfg['gateways']:
                cfg['gateways'][gid] = {}
            cfg['gateways'][gid]['enabled'] = gid in enabled
            if gid == 'manual':
                cfg['gateways'][gid]['pix_key'] = request.form.get('manual_pix_key', '')
                cfg['gateways'][gid]['pix_key_type'] = request.form.get('manual_pix_key_type', 'email')
                cfg['gateways'][gid]['city'] = request.form.get('manual_city', 'Brasil')
                cfg['gateways'][gid]['beneficiary_name'] = request.form.get('manual_beneficiary_name', 'Doação')
            elif gid == 'mercadopago':
                cfg['gateways'][gid]['access_token'] = request.form.get('mercadopago_access_token', '').strip()
            elif gid == '99pay':
                cfg['gateways'][gid]['client_id']     = request.form.get('99pay_client_id', '').strip()
                cfg['gateways'][gid]['client_secret'] = request.form.get('99pay_client_secret', '').strip()
            elif gid == 'stripe':
                cfg['gateways'][gid]['api_key'] = request.form.get('stripe_api_key', '').strip()
                cfg['gateways'][gid]['stripe_pix_enabled'] = 'stripe_pix_enabled' in request.form
            elif gid == 'paypal':
                cfg['gateways'][gid]['client_id']     = request.form.get('paypal_client_id', '').strip()
                cfg['gateways'][gid]['client_secret'] = request.form.get('paypal_client_secret', '').strip()
                cfg['gateways'][gid]['sandbox']       = 'paypal_sandbox' in request.form

        # ── Gateways personalizados (parallel arrays via getlist) ──
        cg_ids             = request.form.getlist('cg_id')
        cg_names           = request.form.getlist('cg_name')
        cg_pix_keys        = request.form.getlist('cg_pix_key')
        cg_pix_key_types   = request.form.getlist('cg_pix_key_type')
        cg_beneficiaries   = request.form.getlist('cg_beneficiary_name')
        cg_cities          = request.form.getlist('cg_city')
        cg_colors          = request.form.getlist('cg_color')
        cg_icons           = request.form.getlist('cg_icon')
        cg_enabled_flags   = request.form.getlist('cg_enabled')  # "1" ou "0" por posição

        custom_gateways = []
        for i, raw_id in enumerate(cg_ids):
            raw_id = raw_id.strip()
            if not raw_id:
                continue
            # Gera ID permanente para novos gateways (prefixo "new_")
            if raw_id.startswith('new_'):
                slug = ''.join(c for c in cg_names[i].lower() if c.isalnum() or c == '_')[:16] or 'custom'
                final_id = f'custom_{slug}_{uuid.uuid4().hex[:6]}'
            else:
                final_id = raw_id
            is_enabled = (cg_enabled_flags[i] == '1') if i < len(cg_enabled_flags) else False
            custom_gateways.append({
                'id':               final_id,
                'name':             (cg_names[i] if i < len(cg_names) else 'Personalizado')[:40],
                'pix_key':          cg_pix_keys[i]      if i < len(cg_pix_keys)      else '',
                'pix_key_type':     cg_pix_key_types[i] if i < len(cg_pix_key_types) else 'email',
                'beneficiary_name': (cg_beneficiaries[i] if i < len(cg_beneficiaries) else 'Doação')[:25],
                'city':             (cg_cities[i]         if i < len(cg_cities)        else 'Brasil')[:15],
                'color':            cg_colors[i] if i < len(cg_colors) else '#32d296',
                'icon':             cg_icons[i]  if i < len(cg_icons)  else 'fa-qrcode',
                'enabled':          is_enabled,
            })

        cfg['custom_gateways'] = custom_gateways
        pgw.save_config(cfg)
        session['_debug_msg'] = 'Configurações de pagamento salvas!'
        return redirect('/payment/admin')

    cfg = pgw.load_config()
    admin_msg = session.pop('_debug_msg', None)
    pix_payments        = admin_store.get_pix_payments(50) if admin['role'] == 'owner' else []
    gateway_backups     = admin_store.get_gateway_backups(10) if admin['role'] == 'owner' else []
    allow_admin_gateway = admin_store.get_allow_admin_gateway() if admin['role'] == 'owner' else False
    any_active = not pgw.are_payments_disabled()
    has_previously_active = bool(cfg.get('previously_active_ids'))
    return render_template('payment_admin.html', cfg=cfg,
                           allow_admin_gateway=allow_admin_gateway,
                           meta=pgw.GATEWAY_META,
                           custom_icons=pgw.CUSTOM_ICONS,
                           custom_colors=pgw.CUSTOM_COLORS,
                           admin_msg=admin_msg,
                           admin=admin,
                           pix_payments=pix_payments,
                           gateway_backups=gateway_backups,
                           any_active=any_active,
                           has_previously_active=has_previously_active)

def _ping_job():
    """BackgroundScheduler job: pings own URL to keep Hugging Face Space awake."""
    url = os.environ.get("APP_URL", "https://a187e2f1-2c9e-49e0-a777-a72c08c34826-00-2l1eepd6uepb0.janeway.replit.dev/ping")
    wait = random.randint(300, 600)
    stats_store.set_val('next_ping', time.time() + wait)
    start = time.time()
    try:
        response = requests.get(url, timeout=10)
        latency_ms = int((time.time() - start) * 1000)
        stats_store.set_val('last_ping_latency', latency_ms)
        app_logger.info(f"🏓 Auto-ping — Status: {response.status_code} ({latency_ms}ms)")
    except Exception as e:
        app_logger.error(f"❌ Ping falhou: {e}")


def auto_ping():
    """Initialises the BackgroundScheduler keep-alive engine (called once at startup)."""
    # Fire immediately so next_ping is set right away
    _ping_job()
    scheduler = BackgroundScheduler(daemon=True)
    # Schedule a recurring jittered ping every 5 minutes (interval) — _ping_job reschedules its
    # own next_ping counter internally, so minute-level granularity is fine here.
    scheduler.add_job(_ping_job, 'interval', minutes=7, jitter=120)
    scheduler.start()
    app_logger.info("🗓️  BackgroundScheduler keep-alive iniciado")


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
    try:
        token = request.cookies.get('donation_token', '')
        if token:
            row = admin_store.get_vip_by_token(token)
            if row:
                donor_nickname     = row.get('nickname')      or None
                donor_vip_level    = row.get('vip_level')     or None
                donor_discriminator = row.get('discriminator') or None
    except Exception:
        pass
    return {
        'max_urls': _get_effective_max_urls(),
        'session_vip_level': _get_session_vip_level(),
        'donor_nickname': donor_nickname,
        'donor_vip_level': donor_vip_level,
        'donor_discriminator': donor_discriminator,
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
    max_urls = _get_effective_max_urls()   # respects VIP cookie boost
    delay_ms = bc['delay_ms']

    urls = [u.strip() for u in raw_urls if u.strip()]
    if len(urls) < 2:
        return jsonify({'error': 'Mínimo de 2 URLs.'}), 400
    if len(urls) > max_urls:
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
    return render_template(
        'admin/ranking.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        rows=rows,
        tiers=tiers,
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
                                                           ip=client_ip, ua=client_ua)
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
                                               ip=client_ip, ua=client_ua)
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
    if not admin_store.has_perm(admin.get('role'), 'gateway'):
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
        'vip_level':     donor.get('vip_level', 5),
        'total_donated': float(donor.get('total_donated', 0)),
    })


@app.route('/ranking')
def public_ranking_view():
    """Public leaderboard — accessible without admin auth."""
    rows = admin_store.get_ranking()
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
    allow_admin_gw = admin_store.get_allow_admin_gateway()
    notifications = admin_store.get_notifications(
        limit=50, viewer_role=admin['role'], admin_id=admin['id'],
        allow_admin_gw=allow_admin_gw
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
    allow_admin_gw = admin_store.get_allow_admin_gateway()
    count = admin_store.count_unread_notifications(admin['id'], admin['role'], allow_admin_gw)
    notifications = admin_store.get_notifications(
        limit=30, viewer_role=admin['role'],
        admin_id=admin['id'], unread_only=False, allow_admin_gw=allow_admin_gw
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
    requests_list = admin_store.get_suspicious_requests(limit=200)
    suspicious_today = admin_store.count_suspicious_today()
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
    flash_msg   = session.pop('_admin_flash', None)
    return render_template(
        'admin/security.html',
        admin=admin,
        role_meta=admin_store.ROLE_META,
        requests_list=requests_list,
        suspicious_today=suspicious_today,
        incidents=incidents,
        forensics_today=forensics_today,
        unique_ips=unique_ips,
        proxy_count=proxy_count,
        flash_msg=flash_msg,
        fmt_ts=admin_store.fmt_ts,
    )


@app.route('/admin/suspicious')
def admin_suspicious_view():
    return redirect('/admin/security')


@app.route('/admin/forensics')
def admin_forensics_view():
    return redirect('/admin/security')


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


@app.route('/admin/forensics/export-dossier/<path:attacker_ip>')
def admin_export_dossier(attacker_ip):
    """Owner-only: generate and download a forensic CSV dossier for a specific attacker IP."""
    admin = g.admin
    if admin['role'] != 'owner':
        abort(404)

    import csv, io
    from datetime import datetime as _dt

    ip   = attacker_ip.strip()
    data = admin_store.get_all_events_by_ip(ip)
    now_str = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    total_events = (len(data['suspicious']) + len(data['brute_force']) +
                    len(data['audit']))

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

    # ── Secao 1: Ataques Suspeitos na URL ──────────────────────────────────
    w.writerow(['=== SECAO 1: ATAQUES SUSPEITOS NA URL ==='])
    if data['suspicious']:
        w.writerow(['Timestamp', 'IP', 'SO', 'Navegador', 'Tipo de Ataque', 'Metodo HTTP',
                    'Payload URL Malicioso (Completo)', 'Ocorrencias'])
        for r in data['suspicious']:
            w.writerow([
                r.get('ts', ''),
                ip,
                r.get('operating_system') or _NO_UA_LABEL,
                r.get('browser_name') or _NO_UA_LABEL,
                r.get('attack_type', ''),
                r.get('method', ''),
                r.get('url_path', ''),
                r.get('count', 1),
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
                r.get('ts', ''),
                ip,
                r.get('operating_system') or _parse_os(r.get('ua', '')),
                r.get('browser_name') or _parse_browser(r.get('ua', '')),
                r.get('hardware_gpu', ''),
                r.get('hardware_ram', ''),
                r.get('hardware_cores', ''),
                r.get('screen_resolution', ''),
                r.get('username_tried', ''),
                r.get('failures', 0),
                r.get('geo_country', ''),
                r.get('geo_region', ''),
                r.get('geo_city', ''),
                r.get('geo_lat', ''),
                r.get('geo_lon', ''),
                r.get('geo_isp', ''),
                r.get('geo_asn', ''),
                'Sim' if r.get('geo_is_proxy') else 'Nao',
                'Sim' if r.get('geo_is_hosting') else 'Nao',
                r.get('canvas_fp', ''),
                r.get('webrtc_ip', ''),
                r.get('ua', ''),
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
                r.get('ts', ''),
                r.get('admin_name', ''),
                r.get('role', ''),
                r.get('action', ''),
                r.get('detail', ''),
            ])
    else:
        w.writerow(['(nenhuma entrada de auditoria encontrada para este IP)'])
    w.writerow([])
    w.writerow(['=== FIM DO DOSSIE FORENSICO ==='])

    admin_store.audit(
        admin['id'], admin['display_name'], admin['role'],
        'dossier_export', f'Dossie exportado para IP {ip} ({total_events} eventos)',
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


@app.route('/admin/gateway-access', methods=['POST'])
def admin_gateway_access_toggle():
    """Owner-only: habilita/desabilita o acesso de Admins ao painel de gateway."""
    admin = g.admin
    if admin['role'] != 'owner':
        return jsonify({'error': 'Sem permissão'}), 403
    data    = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled', False))
    admin_store.set_allow_admin_gateway(enabled)
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           f"{'Ativou' if enabled else 'Desativou'} acesso de Admins ao gateway",
                           None, request.remote_addr)
    return jsonify({'ok': True, 'enabled': enabled})


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
    admin_store.log_action(admin['id'], admin['display_name'], admin['role'],
                           f'Apagou permanentemente a conversa privada #{room_id}',
                           None, request.remote_addr)
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
    url    = request.form.get('url', '').strip()[:500]
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
    url     = request.form.get('url', '').strip()[:500]
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
    url = request.form.get('url', '').strip()
    if not url:
        return jsonify({'ok': False, 'error': 'URL vazia'})
    ok, code = dwh.test_webhook(url)
    return jsonify({'ok': ok, 'code': code})


if __name__ == '__main__':
    app_logger.info("🚀 Iniciando Media Scraper...")
    app_logger.info("🌐 Servidor disponível em: http://localhost:5000")

    # Com debug=True o Werkzeug usa um processo "watcher" que reexecuta este
    # arquivo num processo filho para de fato servir requisições. Se a thread
    # de auto-ping for iniciada no watcher, seus dados (next_ping) ficam presos
    # num processo diferente do que atende o dashboard — por isso só iniciamos
    # threads quando WERKZEUG_RUN_MAIN='true' (processo filho real) OU quando
    # o reloader não está ativo (produção sem debug).
    # Nota: app.debug ainda é False aqui (antes de app.run), então usamos a
    # variável de ambiente WERKZEUG_RUN_MAIN diretamente.
    _run_main = os.environ.get('WERKZEUG_RUN_MAIN')
    # _run_main == 'true'  → processo filho do reloader (arrancar threads)
    # _run_main is None    → sem reloader ativo (produção) OU 1º boot do watcher
    # Para distinguir produção do watcher: watcher define WERKZEUG_SERVER_FD
    _is_watcher = (_run_main is None and os.environ.get('WERKZEUG_SERVER_FD') is not None)
    if _run_main == 'true' or (not _is_watcher and _run_main is None):
        auto_ping()  # initialises BackgroundScheduler keep-alive engine
        app_logger.info("🏓 Auto-ping BackgroundScheduler ativado para manter o servidor online")
        threading.Thread(target=_pix_expiry_worker, daemon=True).start()
        app_logger.info("⏰ Thread de expiração de PIX ativada")

    app.run(host="0.0.0.0", port=5000, debug=True)