"""Discord webhook dispatcher — rich embeds for admin events."""
import json
import re
import threading, requests
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


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
    'owner':     'Proprietário (Owner)',
    'admin':     'Administrador',
    'moderator': 'Moderador',
    'helper':    'Ajudante',
}

_GATEWAY_LABEL = {
    'manual': 'PIX Manual',
    'mercadopago': 'Mercado Pago',
    '99pay': '99Pay',
    'stripe': 'Stripe',
    'paypal': 'PayPal',
}

_GATEWAY_FIELD_LABEL = {
    'enabled': 'Status',
    'pix_key': 'Chave PIX',
    'pix_key_type': 'Tipo da chave',
    'beneficiary_name': 'Beneficiário',
    'city': 'Cidade',
    'access_token': 'Access Token',
    'client_id': 'Client ID',
    'client_secret': 'Client Secret',
    'stripe_test_enabled': 'Modo de teste',
    'stripe_test_secret_key': 'Stripe Key Test',
    'stripe_pix_enabled': 'Pix via Stripe',
    'sandbox': 'Sandbox',
    'name': 'Nome',
    'color': 'Cor',
    'icon': 'Ícone',
}


def _parse_gateway_diff(detail: str) -> dict | None:
    """Parse the compact masked diff written by the gateway audit flow."""
    if not detail or 'Estado anterior (' not in detail:
        return None
    before_line = next(
        (line for line in detail.splitlines()
         if line.startswith('- Estado anterior (')),
        None,
    )
    after_line = next(
        (line for line in detail.splitlines()
         if line.startswith('+ Valor proposto (')),
        None,
    )
    if not before_line or not after_line:
        return None
    match = re.match(r'- Estado anterior \((.*?)\): (.*)$', before_line)
    after_match = re.match(r'\+ Valor proposto \(.*?\): (.*)$', after_line)
    if not match or not after_match:
        return None
    try:
        before = json.loads(match.group(2))
        after = json.loads(after_match.group(1))
    except (TypeError, ValueError):
        return None
    status = 'Aguardando aprovação'
    for line in detail.splitlines():
        if line.startswith('``` ') and line[4:].strip():
            status = line[4:].strip()
            break
    return {
        'scope': match.group(1),
        'before': before,
        'after': after,
        'status': status,
    }


def _display_gateway_value(key: str, value) -> str:
    if isinstance(value, bool):
        if key in ('enabled', 'stripe_pix_enabled'):
            return 'Ativo' if value else 'Desativado'
        return 'Sim' if value else 'Não'
    if value is None or value == '':
        return '—'
    return str(value)


def _gateway_sections(config: dict) -> list[tuple[str, str]]:
    """Turn a masked gateway snapshot into small, readable embed sections."""
    sections = []
    for gateway_id, gateway in (config or {}).get('gateways', {}).items():
        if not isinstance(gateway, dict):
            continue
        title = _GATEWAY_LABEL.get(gateway_id, gateway_id)
        lines = []
        for key, value in gateway.items():
            label = _GATEWAY_FIELD_LABEL.get(key)
            if label:
                lines.append(
                    f'**{label}:** {_display_gateway_value(key, value)}'
                )
        if lines:
            sections.append((title, '\n'.join(lines)[:1000]))

    custom_gateways = (config or {}).get('custom_gateways', [])
    for custom in custom_gateways:
        if not isinstance(custom, dict):
            continue
        title = custom.get('name') or 'Gateway personalizado'
        lines = []
        for key, value in custom.items():
            label = _GATEWAY_FIELD_LABEL.get(key)
            if label and key != 'id':
                lines.append(
                    f'**{label}:** {_display_gateway_value(key, value)}'
                )
        if lines:
            sections.append((str(title)[:80], '\n'.join(lines)[:1000]))

    primary = (config or {}).get('primary_gateway')
    if primary:
        sections.append(('Gateway padrão', f'**Selecionado:** `{primary}`'))
    return sections


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role_label(role: str) -> str:
    return _ROLE_LABEL.get(role, role)


_ACTION_LABEL = {
    'developer_mode_toggle': 'Modo Desenvolvedor',
    'regenerate_access_key': 'Chave de acesso de apoiador',
    'vip_tier_update': 'Configuração de nível VIP',
    'manual_donation_created': 'Doação manual',
    'ranking_delete': 'Entrada no ranking',
    'payment_status_update': 'Status de pagamento',
    'dossier_export_csv': 'Exportação de dossiê de segurança (CSV)',
    'dossier_export_html': 'Exportação de dossiê de segurança (HTML)',
}


def _extract_boolean_detail(detail: str):
    """Read common audit booleans without exposing the internal True/False text."""
    if not detail:
        return None
    match = re.search(
        r'(?:ativo|enabled|active)\s*=\s*(true|false|1|0)',
        str(detail),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).lower() in ('true', '1')


def _humanize_detail(detail: str) -> str:
    """Make legacy audit details readable while retaining useful context."""
    text = str(detail or '').strip()
    if not text:
        return ''
    text = re.sub(r'\bativo\s*=\s*True\b', 'Situação: Ativado', text, flags=re.IGNORECASE)
    text = re.sub(r'\bativo\s*=\s*False\b', 'Situação: Desativado', text, flags=re.IGNORECASE)
    text = re.sub(r'\benabled\s*=\s*True\b', 'Situação: Ativado', text, flags=re.IGNORECASE)
    text = re.sub(r'\benabled\s*=\s*False\b', 'Situação: Desativado', text, flags=re.IGNORECASE)
    text = text.replace('=', ': ')
    return text[:800]


def _admin_action_copy(action: str, detail: str) -> dict:
    """Return user-facing copy for an audit event.

    The stored audit action remains untouched. This only changes the language
    used in Discord, where the reader may not be a programmer.
    """
    raw_action = str(action or '').strip()
    action_key = raw_action.lower()
    label = _ACTION_LABEL.get(action_key)
    if not label:
        label = raw_action.replace('_', ' ').strip().capitalize() or 'Ação administrativa'

    if action_key == 'developer_mode_toggle':
        enabled = _extract_boolean_detail(detail)
        if enabled is True:
            return {
                'label': label,
                'status': '✅ Ativado',
                'description': 'O Modo Desenvolvedor foi ativado pelo responsável.',
                'explanation': 'As ferramentas de desenvolvimento estão disponíveis nesta sessão.',
                'context': '',
            }
        if enabled is False:
            return {
                'label': label,
                'status': '⛔ Desativado',
                'description': 'O Modo Desenvolvedor foi desativado pelo responsável.',
                'explanation': (
                    'As funções de desenvolvedor foram interrompidas nesta sessão. '
                    'As permissões administrativas permanecem inalteradas.'
                ),
                'context': '',
            }

    if raw_action.startswith('Permissão atualizada:'):
        target = raw_action.split(':', 1)[1].strip()
        enabled = _extract_boolean_detail(detail)
        permission_match = re.search(r'^([^=]+)=', str(detail or ''))
        permission = permission_match.group(1).strip() if permission_match else 'permissão administrativa'
        state = 'concedida' if enabled is not False else 'removida'
        return {
            'label': 'Permissão de administrador',
            'status': f'✅ Permissão {state}',
            'description': f'A permissão de {target} foi atualizada.',
            'explanation': f'O acesso configurado foi {state} para este administrador.',
            'context': f'Permissão afetada: {permission}',
        }

    if raw_action.startswith('Webhook Discord '):
        operation = raw_action[len('Webhook Discord '):].strip()
        return {
            'label': 'Configuração de webhook do Discord',
            'status': f'✅ {operation.capitalize()}',
            'description': f'Um webhook do Discord foi {operation}.',
            'explanation': 'Essa alteração modifica o recebimento das notificações administrativas.',
            'context': _humanize_detail(detail),
        }

    return {
        'label': label,
        'status': '✅ Ação registrada',
        'description': f'Foi realizada a ação: **{label}**.',
        'explanation': 'O evento foi registrado para acompanhamento administrativo.',
        'context': _humanize_detail(detail),
    }


def normalize_webhook_url(url: str) -> str:
    """Convert the current Discord hostname to the working legacy hostname.

    Only the exact HTTPS host is changed; the webhook path, query string and
    any other URL are preserved.
    """
    raw = (url or '').strip()
    try:
        parts = urlsplit(raw)
        if parts.scheme.lower() != 'https' or parts.hostname.lower() != 'discord.com':
            return raw
        netloc = 'discordapp.com'
        if parts.port is not None:
            netloc += f':{parts.port}'
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except (AttributeError, ValueError):
        return raw


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
    copy = _admin_action_copy(action, detail)
    fields = [
        {'name': '👤 Responsável', 'value': f'`{admin_name}`',          'inline': True},
        {'name': '🎭 Cargo',       'value': f'`{_role_label(role)}`',   'inline': True},
        {'name': '🛠️ O que foi alterado', 'value': f'`{copy["label"]}`', 'inline': False},
    ]
    gateway_diff = _parse_gateway_diff(detail)
    if gateway_diff:
        status = gateway_diff['status']
        if 'Rejeitado' in status or 'rejeitada' in action:
            color = EVENTS['brute_force']['color']
        elif 'Aprovado' in status or 'aprovada' in action:
            color = EVENTS['maintenance']['color']
        else:
            color = EVENTS['admin_action']['color']
        fields.extend([
            {'name': '📦 Escopo', 'value': f'`{gateway_diff["scope"]}`', 'inline': True},
            {'name': '📌 Status', 'value': f'**{status}**', 'inline': True},
        ])
        before_sections = _gateway_sections(gateway_diff['before'])
        after_sections = _gateway_sections(gateway_diff['after'])
        for title, value in before_sections:
            fields.append({
                'name': f'🔴 ANTES · {title}',
                'value': value,
                'inline': True,
            })
        for title, value in after_sections:
            fields.append({
                'name': f'🟢 DEPOIS · {title}',
                'value': value,
                'inline': True,
            })
    else:
        color = EVENTS['admin_action']['color']
        fields.extend([
            {'name': '📌 Resultado', 'value': copy['status'], 'inline': True},
            {'name': '💡 O que isso significa', 'value': copy['explanation'], 'inline': False},
        ])
        if copy['context']:
            fields.append({
                'name': '📝 Contexto',
                'value': copy['context'],
                'inline': False,
            })
    if ip:
        fields.append({
            'name': '🔎 Registro técnico',
            'value': f'IP de origem registrado: `{ip}`',
            'inline': False,
        })
    embed = {
        'title': '🔌 Alteração de Gateway' if gateway_diff else '⚙️ Ação Administrativa',
        'color': color,
        'fields': fields,
        'footer': {'text': 'Painel Admin · Auditoria de Gateway' if gateway_diff else 'Painel Admin · Auditoria'},
        'timestamp': _now_iso(),
    }
    if not gateway_diff:
        embed['title'] = f'⚙️ {copy["label"]}'
        embed['description'] = copy['description'][:1000]
    return embed


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
        r = requests.post(normalize_webhook_url(url), json={'embeds': [embed]}, timeout=6)
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
