import abc
import base64
import json
import os
import re
import threading
import uuid
from io import BytesIO

import requests

_DEFAULT_CONFIG = {
    'primary_gateway': 'manual',
    'gateways': {
        'manual': {
            'enabled': True,
            'pix_key': '',
            'pix_key_type': 'chave-aleatoria',
            'beneficiary_name': 'Doação',
            'city': 'Brasil',
        },
        'mercadopago': {
            'enabled': False,
            'access_token': '',
        },
        '99pay': {
            'enabled': False,
            'client_id': '',
            'client_secret': '',
        },
        'stripe': {
            'enabled': False,
            'api_key': '',
            'stripe_pix_enabled': True,
        },
        'paypal': {
            'enabled': False,
            'client_id': '',
            'client_secret': '',
            'sandbox': True,
        },
    },
}


# ── Result object ──────────────────────────────────────────────────────────

class PaymentResult:
    def __init__(self, success, payment_id=None, qr_code_base64=None,
                 copy_paste=None, expires_in=3600, gateway=None,
                 amount=None, currency='BRL', error=None, redirect_url=None,
                 is_auth_error=False, raw_api_error=None):
        self.success = success
        self.payment_id = payment_id
        self.qr_code_base64 = qr_code_base64
        self.copy_paste = copy_paste
        self.expires_in = expires_in
        self.gateway = gateway
        self.amount = amount
        self.currency = currency
        self.error = error
        self.redirect_url = redirect_url
        # True when the gateway rejected the request with a 401 Unauthorized
        # (e.g. unverified Mercado Pago production credentials)
        self.is_auth_error = is_auth_error
        # Raw API error payload for webhook alerting (not exposed to users)
        self.raw_api_error = raw_api_error

    def to_dict(self):
        return {
            'success': self.success,
            'payment_id': self.payment_id,
            'qr_code_base64': self.qr_code_base64,
            'copy_paste': self.copy_paste,
            'expires_in': self.expires_in,
            'gateway': self.gateway,
            'amount': self.amount,
            'currency': self.currency,
            'error': self.error,
            'redirect_url': self.redirect_url,
        }


# ── Abstract base ──────────────────────────────────────────────────────────

class BaseGateway(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self): pass

    @property
    @abc.abstractmethod
    def gateway_id(self): pass

    @property
    def supports_pix(self): return True

    @abc.abstractmethod
    def create_pix_payment(self, amount_brl: float, description: str = 'Doação') -> PaymentResult:
        pass

    def check_payment_status(self, payment_id: str) -> dict:
        return {'status': 'unknown'}

    def validate_credentials(self) -> bool:
        return True


# ── QR Code helper ─────────────────────────────────────────────────────────

def _build_qr_b64(payload: str) -> str | None:
    try:
        import qrcode
        img = qrcode.make(payload)
        buf = BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode('utf-8')
    except Exception:
        return None


# ── PIX EMV payload builder ────────────────────────────────────────────────

def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"

def _crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for byte in data.encode('utf-8'):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f'{crc:04X}'

def build_pix_emv(pix_key: str, name: str, city: str, amount: float, txid: str = 'DOACAO') -> str:
    merchant_account = _tlv('00', 'BR.GOV.BCB.PIX') + _tlv('01', pix_key[:77])
    name = name[:25] or 'Doacao'
    city = city[:15] or 'Brasil'
    amount_str = f'{amount:.2f}'

    payload = (
        _tlv('00', '01') +
        _tlv('26', merchant_account) +
        _tlv('52', '0000') +
        _tlv('53', '986') +
        _tlv('54', amount_str) +
        _tlv('58', 'BR') +
        _tlv('59', name) +
        _tlv('60', city) +
        _tlv('62', _tlv('05', txid[:25]))
    )
    return payload + '6304' + _crc16_ccitt(payload + '6304')


# ── Gateways ───────────────────────────────────────────────────────────────

def _sanitize_pix_key(key: str, key_type: str) -> str:
    """Normaliza a chave PIX conforme o tipo antes de montar o EMV."""
    key = key.strip()
    if key_type in ('cpf', 'cnpj'):
        return re.sub(r'\D', '', key)          # só dígitos
    if key_type == 'phone':
        digits = re.sub(r'\D', '', key)        # remove tudo que não é dígito
        if not digits.startswith('55'):
            digits = '55' + digits
        return '+' + digits                    # formato: +5511999999999
    return key                                 # email e chave aleatória: sem alteração


class ManualPIXGateway(BaseGateway):
    def __init__(self, config: dict):
        self.pix_key      = config.get('pix_key', '')
        self.pix_key_type = config.get('pix_key_type', 'email')
        self.beneficiary_name = config.get('beneficiary_name', 'Doação')
        self.city = config.get('city', 'Brasil')

    @property
    def name(self): return 'PIX Manual'
    @property
    def gateway_id(self): return 'manual'

    def create_pix_payment(self, amount_brl: float, description: str = 'Doação') -> PaymentResult:
        pix_key = _sanitize_pix_key(self.pix_key or 'chave@pix.com', self.pix_key_type)
        emv = build_pix_emv(pix_key, self.beneficiary_name, self.city, amount_brl)
        return PaymentResult(
            success=True,
            payment_id=f'MANUAL-{uuid.uuid4().hex[:8].upper()}',
            qr_code_base64=_build_qr_b64(emv),
            copy_paste=emv,
            expires_in=3600,
            gateway='manual',
            amount=amount_brl,
            currency='BRL',
        )

    def validate_credentials(self) -> bool:
        return bool(self.pix_key)


class MercadoPagoGateway(BaseGateway):
    BASE = 'https://api.mercadopago.com'

    def __init__(self, config: dict):
        # Prioridade: valor salvo no painel (banco) > variável de ambiente como fallback
        self.access_token = (
            config.get('access_token', '') or
            os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')
        )

    @property
    def name(self): return 'Mercado Pago'
    @property
    def gateway_id(self): return 'mercadopago'

    def create_pix_payment(self, amount_brl: float, description: str = 'Doação') -> PaymentResult:
        if not self.access_token:
            return PaymentResult(success=False, error='Token do Mercado Pago não configurado')
        try:
            resp = requests.post(
                f'{self.BASE}/v1/payments',
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'X-Idempotency-Key': str(uuid.uuid4()),
                    'Content-Type': 'application/json',
                },
                json={
                    'transaction_amount': round(amount_brl, 2),
                    'description': description[:100],
                    'payment_method_id': 'pix',
                    'payer': {'email': 'doador@doacao.com'},
                },
                timeout=30,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                pix = data.get('point_of_interaction', {}).get('transaction_data', {})
                return PaymentResult(
                    success=True,
                    payment_id=str(data.get('id')),
                    qr_code_base64=pix.get('qr_code_base64'),
                    copy_paste=pix.get('qr_code'),
                    expires_in=3600,
                    gateway='mercadopago',
                    amount=amount_brl,
                    currency='BRL',
                )
            # 401 Unauthorized — unverified / unconfigured production credentials
            if resp.status_code == 401:
                try:
                    raw_err = resp.json()
                except Exception:
                    raw_err = {'raw': resp.text[:500]}
                return PaymentResult(
                    success=False,
                    error='Não implementado',
                    is_auth_error=True,
                    raw_api_error=raw_err,
                )
            return PaymentResult(success=False, error=resp.json().get('message', f'Erro HTTP {resp.status_code}'))
        except requests.Timeout:
            return PaymentResult(success=False, error='Timeout ao conectar com Mercado Pago')
        except Exception as e:
            return PaymentResult(success=False, error=str(e))

    def check_payment_status(self, payment_id: str) -> dict:
        if not self.access_token:
            return {'status': 'error', 'message': 'Token não configurado'}
        try:
            resp = requests.get(
                f'{self.BASE}/v1/payments/{payment_id}',
                headers={'Authorization': f'Bearer {self.access_token}'},
                timeout=15,
            )
            if resp.ok:
                d = resp.json()
                return {'status': d.get('status'), 'detail': d.get('status_detail')}
            return {'status': 'error'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def validate_credentials(self) -> bool:
        return bool(self.access_token)


class NovenovePagGateway(BaseGateway):
    def __init__(self, config: dict):
        # Prioridade: valor salvo no painel (banco) > variável de ambiente como fallback
        self.client_id = config.get('client_id', '') or os.environ.get('NOVENOVE_CLIENT_ID', '')
        self.client_secret = config.get('client_secret', '') or os.environ.get('NOVENOVE_CLIENT_SECRET', '')

    @property
    def name(self): return '99Pay'
    @property
    def gateway_id(self): return '99pay'

    def create_pix_payment(self, amount_brl: float, description: str = 'Doação') -> PaymentResult:
        if not self.client_id or not self.client_secret:
            return PaymentResult(success=False, error='Credenciais 99Pay não configuradas. Preencha Client ID e Client Secret no painel de pagamentos.')
        return PaymentResult(success=False, error='99Pay: aguardando homologação. Configure e ative seu contrato em 99pay.com.br para liberar este gateway.')

    def validate_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)


class StripeGateway(BaseGateway):
    def __init__(self, config: dict):
        # Prioridade: valor salvo no painel (banco) > variável de ambiente como fallback
        self.api_key = config.get('api_key', '') or os.environ.get('STRIPE_SECRET_KEY', '')
        # Admin-controlled toggle: allow disabling the Pix tab for Stripe
        # specifically (its Pix support is region-limited / under review),
        # routing customers straight to the standard card checkout instead.
        self.pix_enabled = bool(config.get('stripe_pix_enabled', True))

    @property
    def name(self): return 'Stripe'
    @property
    def gateway_id(self): return 'stripe'
    @property
    def supports_pix(self): return self.pix_enabled

    def create_pix_payment(self, amount_brl: float, description: str = 'Doação') -> PaymentResult:
        if not self.api_key:
            return PaymentResult(success=False, error='Chave Stripe não configurada')
        try:
            import stripe
            stripe.api_key = self.api_key
            intent = stripe.PaymentIntent.create(
                amount=int(round(amount_brl * 100)),
                currency='brl',
                payment_method_types=['pix'],
                metadata={'description': description},
            )
            pix = intent.get('next_action', {}).get('pix_display_qr_code', {})
            return PaymentResult(
                success=True,
                payment_id=intent['id'],
                qr_code_base64=None,
                copy_paste=pix.get('data'),
                expires_in=intent.get('expires_after_seconds', 3600),
                gateway='stripe',
                amount=amount_brl,
                currency='BRL',
            )
        except Exception as e:
            err = str(e)
            if 'No such payment_method' in err or 'pix' in err.lower():
                return PaymentResult(success=False, error='Stripe: PIX não habilitado nesta conta. Ative PIX no painel Stripe Brasil.')
            return PaymentResult(success=False, error=f'Stripe: {err}')

    def create_card_payment(self, amount_brl: float, description: str = 'Doação',
                             success_url: str | None = None, cancel_url: str | None = None) -> PaymentResult:
        """Cartão de crédito / métodos alternativos via Stripe Checkout (redirect)."""
        if not self.api_key:
            return PaymentResult(success=False, error='Chave Stripe não configurada')
        try:
            import stripe
            stripe.api_key = self.api_key
            session = stripe.checkout.Session.create(
                mode='payment',
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'brl',
                        'product_data': {'name': description},
                        'unit_amount': int(round(amount_brl * 100)),
                    },
                    'quantity': 1,
                }],
                success_url=success_url or 'https://example.com/?payment=success',
                cancel_url=cancel_url or 'https://example.com/?payment=cancel',
            )
            return PaymentResult(
                success=True,
                payment_id=session['id'],
                gateway='stripe',
                amount=amount_brl,
                currency='BRL',
                redirect_url=session['url'],
            )
        except Exception as e:
            return PaymentResult(success=False, error=f'Stripe: {e}')

    def validate_credentials(self) -> bool:
        return bool(self.api_key)


class PayPalGateway(BaseGateway):
    def __init__(self, config: dict):
        # Prioridade: valor salvo no painel (banco) > variável de ambiente como fallback
        self.client_id = config.get('client_id', '') or os.environ.get('PAYPAL_CLIENT_ID', '')
        self.client_secret = config.get('client_secret', '') or os.environ.get('PAYPAL_CLIENT_SECRET', '')
        self.sandbox = config.get('sandbox', True)

    @property
    def name(self): return 'PayPal'
    @property
    def gateway_id(self): return 'paypal'
    @property
    def supports_pix(self): return False

    def create_pix_payment(self, amount_brl: float, description: str = 'Doação',
                          return_url: str | None = None, cancel_url: str | None = None) -> PaymentResult:
        if not self.client_id or not self.client_secret:
            return PaymentResult(success=False, error='Credenciais PayPal não configuradas. Preencha Client ID e Client Secret no painel.')
        base = 'https://api-m.sandbox.paypal.com' if self.sandbox else 'https://api-m.paypal.com'
        try:
            token_resp = requests.post(
                f'{base}/v1/oauth2/token',
                auth=(self.client_id, self.client_secret),
                data='grant_type=client_credentials',
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10,
            )
            if token_resp.status_code != 200:
                return PaymentResult(success=False, error=f'PayPal: falha na autenticação ({token_resp.status_code}). Verifique Client ID e Secret.')
            access_token = token_resp.json().get('access_token', '')
            application_context = {'user_action': 'PAY_NOW'}
            if return_url:
                application_context['return_url'] = return_url
            if cancel_url:
                application_context['cancel_url'] = cancel_url
            order_resp = requests.post(
                f'{base}/v2/checkout/orders',
                headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
                json={
                    'intent': 'CAPTURE',
                    'purchase_units': [{
                        'amount': {'currency_code': 'BRL', 'value': f'{amount_brl:.2f}'},
                        'description': description,
                    }],
                    'application_context': application_context,
                },
                timeout=10,
            )
            if order_resp.status_code not in (200, 201):
                return PaymentResult(success=False, error=f'PayPal: erro ao criar ordem ({order_resp.status_code}).')
            order = order_resp.json()
            approval_url = next((l['href'] for l in order.get('links', []) if l['rel'] == 'approve'), None)
            if not approval_url:
                return PaymentResult(success=False, error='PayPal: URL de aprovação não retornada.')
            return PaymentResult(
                success=True,
                payment_id=order['id'],
                gateway='paypal',
                amount=amount_brl,
                currency='BRL',
                redirect_url=approval_url,
            )
        except requests.exceptions.Timeout:
            return PaymentResult(success=False, error='PayPal: timeout ao conectar. Tente novamente.')
        except Exception as e:
            return PaymentResult(success=False, error=f'PayPal: {e}')

    def capture_order(self, order_id: str) -> dict:
        """Captura (finaliza) uma ordem PayPal já aprovada pelo comprador.
        Chamado pela rota de retorno (/payment/paypal/capture) após o redirect
        de volta do checkout PayPal — sem isso, a compra fica só 'aprovada',
        nunca 'capturada' (cobrada de fato)."""
        base = 'https://api-m.sandbox.paypal.com' if self.sandbox else 'https://api-m.paypal.com'
        token_resp = requests.post(
            f'{base}/v1/oauth2/token',
            auth=(self.client_id, self.client_secret),
            data='grant_type=client_credentials',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10,
        )
        if token_resp.status_code != 200:
            return {'status': 'error'}
        access_token = token_resp.json().get('access_token', '')
        cap_resp = requests.post(
            f'{base}/v2/checkout/orders/{order_id}/capture',
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            timeout=10,
        )
        if cap_resp.status_code not in (200, 201):
            return {'status': 'error'}
        return cap_resp.json()

    def validate_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)


# ── Gateway personalizado ──────────────────────────────────────────────────

class CustomGateway(BaseGateway):
    """Gateway PIX completamente configurável pelo usuário."""

    def __init__(self, config: dict):
        self._id   = config.get('id', 'custom_unknown')
        self._name = config.get('name', 'Gateway Personalizado')
        self.pix_key          = config.get('pix_key', '')
        self.pix_key_type     = config.get('pix_key_type', 'email')
        self.beneficiary_name = config.get('beneficiary_name', 'Doação')[:25]
        self.city             = config.get('city', 'Brasil')[:15]

    @property
    def name(self):       return self._name
    @property
    def gateway_id(self): return self._id

    def create_pix_payment(self, amount_brl: float, description: str = 'Doação') -> PaymentResult:
        pix_key = _sanitize_pix_key(self.pix_key or 'chave@pix.com', self.pix_key_type)
        emv = build_pix_emv(pix_key, self.beneficiary_name, self.city, amount_brl)
        return PaymentResult(
            success=True,
            payment_id=f'{self._id.upper()[:8]}-{uuid.uuid4().hex[:8].upper()}',
            qr_code_base64=_build_qr_b64(emv),
            copy_paste=emv,
            expires_in=3600,
            gateway=self._id,
            amount=amount_brl,
            currency='BRL',
        )

    def validate_credentials(self) -> bool:
        return bool(self.pix_key)


# ── Registry ───────────────────────────────────────────────────────────────

_GATEWAY_CLASSES = {
    'manual': ManualPIXGateway,
    'mercadopago': MercadoPagoGateway,
    '99pay': NovenovePagGateway,
    'stripe': StripeGateway,
    'paypal': PayPalGateway,
}

GATEWAY_META = {
    'manual':      {'name': 'PIX Manual',   'icon': 'fa-qrcode',        'color': '#32d296', 'pix': True,  'desc': 'QR Code gerado com sua chave PIX cadastrada — sem API externa'},
    'mercadopago': {'name': 'Mercado Pago', 'icon': 'fa-credit-card',   'color': '#00b1ea', 'pix': True,  'desc': 'PIX dinâmico via API do Mercado Pago (requer access token)'},
    '99pay':       {'name': '99Pay',        'icon': 'fa-mobile-screen', 'color': '#f5a623', 'pix': True,  'desc': 'PIX via 99Pay (requer contrato e credenciais API)'},
    'stripe':      {'name': 'Stripe',       'icon': 'fa-stripe',        'color': '#635bff', 'pix': True,  'desc': 'PIX via Stripe (requer conta Stripe Brasil habilitada para PIX)'},
    'paypal':      {'name': 'PayPal',       'icon': 'fa-paypal',        'color': '#003087', 'pix': False, 'desc': 'Pagamentos internacionais via PayPal (não suporta PIX)'},
}

# Ícones disponíveis para gateways personalizados
CUSTOM_ICONS = [
    ('fa-qrcode',            'QR Code'),
    ('fa-building-columns',  'Banco'),
    ('fa-piggy-bank',        'Cofre'),
    ('fa-wallet',            'Carteira'),
    ('fa-money-bill-wave',   'Dinheiro'),
    ('fa-hand-holding-dollar','Doação'),
    ('fa-star',              'Estrela'),
    ('fa-heart',             'Coração'),
    ('fa-bolt',              'Raio'),
    ('fa-gem',               'Diamante'),
    ('fa-briefcase',         'Maleta'),
    ('fa-store',             'Loja'),
    ('fa-mobile-screen',     'Celular'),
    ('fa-globe',             'Global'),
    ('fa-leaf',              'Folha'),
]

# Paleta de cores para gateways personalizados
CUSTOM_COLORS = [
    '#32d296', '#58a6ff', '#f85149', '#f0883e',
    '#a371f7', '#3fb950', '#ffa657', '#e6edf3',
    '#ff7b72', '#79c0ff', '#d2a8ff', '#56d364',
]


# ── Config I/O ─────────────────────────────────────────────────────────────

def _merge_saved_config(saved: dict) -> dict:
    """Merge a saved config dict into the default structure."""
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))
    cfg.update({k: v for k, v in saved.items() if k != 'gateways'})
    for gid, defaults in _DEFAULT_CONFIG['gateways'].items():
        merged = dict(defaults)
        merged.update(saved.get('gateways', {}).get(gid, {}))
        cfg['gateways'][gid] = merged
    for gid, vals in saved.get('gateways', {}).items():
        if gid not in cfg['gateways']:
            cfg['gateways'][gid] = vals
    if 'custom_gateways' not in cfg:
        cfg['custom_gateways'] = []
    return cfg


def load_config() -> dict:
    """Carrega a configuração de gateways exclusivamente do banco de dados
    (tabela admin_settings via DATABASE_URL) — sobrevive a reboots do container."""
    try:
        import admin_store as _store
        db_cfg = _store.get_gateway_config()
        if db_cfg:
            return _merge_saved_config(db_cfg)
    except Exception:
        pass
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))
    cfg['custom_gateways'] = []
    return cfg

_config_lock = threading.Lock()

def save_config(cfg: dict):
    """Persiste a configuração de gateways exclusivamente no banco de dados."""
    with _config_lock:
        import admin_store as _store
        _store.set_gateway_config(cfg)

def load_config_locked() -> dict:
    """load_config com lock — usar quando há chance de write concorrente."""
    with _config_lock:
        return load_config()

def get_gateway(gateway_id: str) -> BaseGateway | None:
    cfg = load_config()
    # Gateways padrão
    cls = _GATEWAY_CLASSES.get(gateway_id)
    if cls:
        gw_cfg = cfg.get('gateways', {}).get(gateway_id, {})
        return cls(gw_cfg)
    # Gateways personalizados (id começa com "custom_" ou bate em custom_gateways)
    for cg in cfg.get('custom_gateways', []):
        if cg.get('id') == gateway_id:
            return CustomGateway(cg)
    return None

def get_custom_gateways(cfg: dict | None = None) -> list[dict]:
    """Retorna lista de gateways personalizados do config."""
    if cfg is None:
        cfg = load_config()
    return cfg.get('custom_gateways', [])

def are_payments_disabled() -> bool:
    """Returns True if ALL payment methods (Pix or card) are currently disabled."""
    return len(get_enabled_pix_gateways()) == 0 and len(get_enabled_card_gateways()) == 0


def _gateway_supports_pix(gid: str, gw_cfg: dict, meta: dict) -> bool:
    """Resolve dinamicamente se um gateway habilitado deve aparecer na aba Pix.
    Para a maioria dos gateways isso é fixo (GATEWAY_META), mas o Stripe tem
    um toggle de admin (`stripe_pix_enabled`) que pode desativar Pix e deixá-lo
    disponível apenas como método de cartão."""
    if gid == 'stripe':
        return bool(gw_cfg.get('stripe_pix_enabled', True))
    return meta.get('pix', True)


def get_enabled_pix_gateways() -> list[dict]:
    cfg = load_config()
    primary = cfg.get('primary_gateway', 'manual')
    result = []
    for gid, meta in GATEWAY_META.items():
        gw_cfg = cfg.get('gateways', {}).get(gid, {})
        if not gw_cfg.get('enabled', False):
            continue
        if not _gateway_supports_pix(gid, gw_cfg, meta):
            continue  # gateway sem Pix habilitado não aparece na aba Pix
        result.append({'id': gid, 'name': meta['name'], 'icon': meta['icon'], 'primary': gid == primary})
    # Gateways personalizados (sempre Pix)
    for cg in cfg.get('custom_gateways', []):
        if cg.get('enabled', False) and cg.get('pix_key'):
            result.append({
                'id':   cg['id'],
                'primary': cg['id'] == primary,
                'name': cg.get('name', 'Personalizado'),
                'icon': cg.get('icon', 'fa-qrcode'),
            })
    if not result:
        # Fallback para manual somente se estiver explicitamente habilitado (ou sem config)
        manual_cfg = cfg.get('gateways', {}).get('manual', {})
        if manual_cfg.get('enabled', True):
            result.append({'id': 'manual', 'name': 'PIX Manual', 'icon': 'fa-qrcode', 'primary': 'manual' == primary})
    return result


def get_enabled_card_gateways() -> list[dict]:
    """Gateways de cartão de crédito / métodos alternativos (PayPal sempre;
    Stripe sempre que estiver habilitado — Pix e Cartão operam em paralelo,
    o toggle `stripe_pix_enabled` decide apenas se ele também aparece na aba Pix,
    nunca remove sua presença na aba Cartão)."""
    cfg = load_config()
    primary = cfg.get('primary_gateway', 'manual')
    result = []
    for gid, meta in GATEWAY_META.items():
        if gid not in ('stripe', 'paypal'):
            continue  # únicos gateways com fluxo de cartão implementado
        gw_cfg = cfg.get('gateways', {}).get(gid, {})
        if not gw_cfg.get('enabled', False):
            continue
        result.append({'id': gid, 'name': meta['name'], 'icon': meta['icon'], 'primary': gid == primary})
    return result
