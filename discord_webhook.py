"""Discord webhook dispatcher — rich embeds for admin events."""
import threading, requests
from datetime import datetime, timezone


EVENTS = {
    'admin_login':    {'label': '🔑 Login de Admin',          'color': 0x22c55e},
    'admin_logout':   {'label': '🚪 Logout de Admin',          'color': 0x6b7280},
    'brute_force':    {'label': '🚨 Brute Force Detectado',    'color': 0xef4444},
    'admin_action':   {'label': '⚙️ Ação Administrativa',     'color': 0x3b82f6},
    'payment':        {'label': '💳 Evento de Pagamento',      'color': 0xa855f7},
    'maintenance':    {'label': '🔧 Modo de Manutenção',       'color': 0xf59e0b},
    'gateway_backup': {'label': '💾 Backup de Gateway',        'color': 0x14b8a6},
}

_ROLE_LABEL = {
    'owner':     'Owner',
    'admin':     'Admin',
    'moderator': 'Moderador',
    'helper':    'Ajudante',
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role_label(role: str) -> str:
    return _ROLE_LABEL.get(role, role)


def _geo_fields(ip: str, geo=None) -> list:
    fields = [{'name': '🌐 IP', 'value': f'`{ip}`', 'inline': True}]
    if geo:
        city    = geo.get('city', '?')
        region  = geo.get('region', '?')
        country = geo.get('country', '?')
        org     = geo.get('org', '')
        lat     = geo.get('lat', '')
        lon     = geo.get('lon', '')
        fields.append({'name': '📍 Localização', 'value': f'{city}, {region}, {country}', 'inline': True})
        if org:
            fields.append({'name': '🏢 ISP / Org', 'value': f'`{org[:50]}`', 'inline': True})
        if lat and lon:
            fields.append({
                'name': '🗺️ Mapa',
                'value': f'[Ver no Google Maps](https://maps.google.com/?q={lat},{lon})',
                'inline': True,
            })
    return fields


def build_login_embed(admin_name: str, role: str, ip: str, geo=None, ua: str = None) -> dict:
    fields = [
        {'name': '👤 Administrador', 'value': f'`{admin_name}`',          'inline': True},
        {'name': '🎭 Cargo',         'value': f'`{_role_label(role)}`',    'inline': True},
        {'name': '\u200b',            'value': '\u200b',                    'inline': True},
    ] + _geo_fields(ip, geo)
    if ua:
        fields.append({'name': '💻 User Agent', 'value': f'```{ua[:200]}```', 'inline': False})
    return {
        'title': '🔑 Login de Administrador',
        'color': EVENTS['admin_login']['color'],
        'fields': fields,
        'footer': {'text': 'Painel Admin · Login bem-sucedido'},
        'timestamp': _now_iso(),
    }


def build_logout_embed(admin_name: str, role: str, ip: str) -> dict:
    return {
        'title': '🚪 Logout de Administrador',
        'color': EVENTS['admin_logout']['color'],
        'fields': [
            {'name': '👤 Administrador', 'value': f'`{admin_name}`',       'inline': True},
            {'name': '🎭 Cargo',         'value': f'`{_role_label(role)}`', 'inline': True},
            {'name': '🌐 IP',            'value': f'`{ip}`',                'inline': True},
        ],
        'footer': {'text': 'Painel Admin · Sessão encerrada'},
        'timestamp': _now_iso(),
    }


def build_brute_force_embed(ip: str, fails: int, geo=None, ua: str = None,
                             username: str = None) -> dict:
    fields = [
        {'name': '🔴 Status',       'value': '**IP Bloqueado por 15 min**',       'inline': True},
        {'name': '🔢 Tentativas',   'value': f'**`{fails}`** falhas consecutivas', 'inline': True},
        {'name': '\u200b',           'value': '\u200b',                             'inline': True},
    ]
    if username:
        fields.append({'name': '🎯 Usuário Alvo', 'value': f'`{username}`', 'inline': True})
        fields.append({'name': '\u200b',            'value': '\u200b',        'inline': True})
        fields.append({'name': '\u200b',            'value': '\u200b',        'inline': True})
    fields += _geo_fields(ip, geo)
    if ua:
        fields.append({'name': '💻 Dispositivo / User-Agent', 'value': f'```{ua[:200]}```', 'inline': False})
    return {
        'title': '🚨 Ataque de Brute Force Detectado',
        'description': (
            f'O IP `{ip}` foi **bloqueado por 15 minutos** após {fails} tentativas de login falhas.\n'
            + (f'> **Usuário alvo:** `{username}`' if username else '')
        ),
        'color': EVENTS['brute_force']['color'],
        'fields': fields,
        'footer': {'text': 'Painel Admin · Segurança'},
        'timestamp': _now_iso(),
    }


def build_action_embed(admin_name: str, role: str, action: str,
                       detail: str = None, ip: str = None) -> dict:
    fields = [
        {'name': '👤 Responsável', 'value': f'`{admin_name}`',          'inline': True},
        {'name': '🎭 Cargo',       'value': f'`{_role_label(role)}`',   'inline': True},
        {'name': '⚙️ Ação',        'value': f'`{action}`',              'inline': False},
    ]
    if detail:
        fields.append({'name': '📝 Detalhe', 'value': f'{detail[:300]}', 'inline': False})
    if ip:
        fields.append({'name': '🌐 IP', 'value': f'`{ip}`', 'inline': True})
    return {
        'title': '⚙️ Ação Administrativa',
        'color': EVENTS['admin_action']['color'],
        'fields': fields,
        'footer': {'text': 'Painel Admin · Auditoria'},
        'timestamp': _now_iso(),
    }


def build_payment_embed(action: str, description: str = None,
                        amount=None, gateway: str = None,
                        payment_id: str = None, access_key: str = None,
                        detail: str = None) -> dict:
    """Rich payment embed with full audit metadata.

    Args:
        action:      Event type tag (e.g. 'novo_doador', 'pix_gerado').
        description: Celebratory / context sentence shown in the embed body.
        amount:      Donation amount — float (formatted internally to 'R$ X,XX')
                     or pre-formatted string (any existing 'R$' prefix is kept,
                     never duplicated).
        gateway:     Gateway name string (e.g. 'Stripe', 'manual').
        payment_id:  Immutable payment reference code for admin cross-examination.
        access_key:  SHA-256 supporter token (64-char hex) — copyable directly
                     from Discord for support ticket verification.
        detail:      Legacy plain-text fallback field (deprecated, prefer description).
    """
    fields = [{'name': '💳 Evento', 'value': f'`{action}`', 'inline': False}]

    # ── Valor — normalise amount to avoid "R$ R$ X,XX" duplication ──
    if amount is not None:
        if isinstance(amount, (int, float)):
            _amt = 'R$ ' + f'{amount:.2f}'.replace('.', ',')
        else:
            _s = str(amount).strip()
            _amt = _s if _s.startswith('R$') else ('R$ ' + _s)
        fields.append({'name': '💰 Valor', 'value': _amt, 'inline': True})

    if gateway:
        fields.append({'name': '🔌 Gateway Utilizada',
                       'value': f'`{gateway}`', 'inline': True})

    if payment_id:
        fields.append({'name': '🆔 ID / Referência de Pagamento',
                       'value': f'`{str(payment_id)[:150]}`', 'inline': False})

    if access_key:
        fields.append({'name': '🔑 Chave SHA-256 Operacional',
                       'value': f'```{str(access_key)[:64]}```', 'inline': False})

    if detail:  # legacy compat — retained for callers not yet migrated
        fields.append({'name': '📝 Detalhe', 'value': f'{detail[:300]}', 'inline': False})

    embed = {
        'title': '💳 Evento de Pagamento',
        'color': EVENTS['payment']['color'],
        'fields': fields,
        'footer': {'text': 'Painel Admin · Pagamentos'},
        'timestamp': _now_iso(),
    }
    if description:
        embed['description'] = description[:1000]
    return embed


def build_maintenance_embed(enabled: bool, message: str,
                             set_by: str, ip: str = None) -> dict:
    status = '🟢 **Ativado**' if enabled else '🔴 **Desativado**'
    fields = [
        {'name': '🔧 Status', 'value': status,           'inline': True},
        {'name': '👤 Por',    'value': f'`{set_by}`',    'inline': True},
    ]
    if message:
        fields.append({'name': '💬 Mensagem', 'value': f'{message[:200]}', 'inline': False})
    if ip:
        fields.append({'name': '🌐 IP', 'value': f'`{ip}`', 'inline': True})
    return {
        'title': '🔧 Modo de Manutenção',
        'color': EVENTS['maintenance']['color'],
        'fields': fields,
        'footer': {'text': 'Painel Admin · Manutenção'},
        'timestamp': _now_iso(),
    }


def build_gateway_backup_embed(action: str, admin_name: str,
                                backup_id=None, ip: str = None) -> dict:
    fields = [
        {'name': '💾 Ação',       'value': f'`{action}`',      'inline': True},
        {'name': '👤 Por',        'value': f'`{admin_name}`',  'inline': True},
    ]
    if backup_id is not None:
        fields.append({'name': '🆔 Backup ID', 'value': f'`{backup_id}`', 'inline': True})
    if ip:
        fields.append({'name': '🌐 IP', 'value': f'`{ip}`', 'inline': True})
    return {
        'title': '💾 Backup de Gateway',
        'color': EVENTS['gateway_backup']['color'],
        'fields': fields,
        'footer': {'text': 'Painel Admin · Gateway'},
        'timestamp': _now_iso(),
    }


def _post_to_discord(url: str, embed: dict) -> int | None:
    try:
        r = requests.post(url, json={'embeds': [embed]}, timeout=6)
        return r.status_code
    except Exception:
        return None


def fire(event_type: str, embed: dict):
    """Send embed to all enabled webhooks subscribed to this event. Non-blocking."""
    import admin_store as _store
    webhooks = _store.get_webhooks_for_event(event_type)
    for wh in webhooks:
        threading.Thread(target=_post_to_discord, args=(wh['url'], embed), daemon=True).start()


def test_webhook(url: str) -> tuple:
    """Send a test embed. Returns (ok: bool, status_code: int | None)."""
    embed = {
        'title': '✅ Teste de Webhook',
        'description': 'Conexão bem-sucedida! Seu webhook está configurado corretamente e recebendo eventos do painel.',
        'color': 0x22c55e,
        'fields': [
            {'name': '📡 Status',  'value': '`Conectado com sucesso`', 'inline': True},
            {'name': '⏰ Horário', 'value': f'`{_now_iso()[:19].replace("T"," ")} UTC`', 'inline': True},
        ],
        'footer': {'text': 'Painel Admin · Teste de Webhook'},
        'timestamp': _now_iso(),
    }
    code = _post_to_discord(url, embed)
    return (code is not None and 200 <= code < 300, code)
