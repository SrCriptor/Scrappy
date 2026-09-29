import threading
import time

import admin_store as _store

_lock = threading.Lock()

start_time = time.time()

# ── Chaves que persistem no banco (tabela dashboard_metrics) ───────────────
# 'next_ping' agora também persiste — evita que o cronômetro "Próximo Ping"
# reinicie do zero quando o processo reinicia (ex.: restart do workflow).
_PERSISTENT_KEYS = ('views', 'searches', 'images', 'videos', 'downloads', 'last_scrape', 'next_ping')

# ── Estado em memória (cache local, espelha o banco) ────────────────────────
_real = {
    'views': 0,
    'searches': 0,
    'images': 0,
    'videos': 0,
    'downloads': 0,
    'last_scrape': None,   # timestamp float
    'next_ping': None,     # timestamp float — persiste no banco (dashboard_metrics)
}


# ── Carga inicial — restaura valores do banco de dados ─────────────────────
# Executada em thread daemon para não bloquear a inicialização do Flask em
# ambientes com conexão DB lenta (ex.: cold start no Hugging Face).
def _boot_load():
    try:
        saved = _store.get_all_metrics()
    except Exception:
        saved = {}
    with _lock:
        for k in _PERSISTENT_KEYS:
            if k in saved and saved[k] is not None:
                _real[k] = saved[k]

threading.Thread(target=_boot_load, daemon=True, name='stats-boot-load').start()

# ── API pública ───────────────────────────────────────────────────────────
def increment(key, amount=1):
    with _lock:
        prev = _real.get(key) or 0
        _real[key] = prev + amount
        if key in _PERSISTENT_KEYS:
            try:
                _store.increment_metric(key, amount)
            except Exception:
                pass

def set_val(key, val):
    with _lock:
        _real[key] = val
        if key in _PERSISTENT_KEYS:
            try:
                _store.set_metric(key, val)
            except Exception:
                pass

def get_real():
    with _lock:
        return dict(_real)

def get_overrides():
    try:
        raw = _store.get_all_metrics()
    except Exception:
        raw = {}
    overrides = {}
    for k in _PERSISTENT_KEYS:
        v = raw.get(f'override_{k}')
        if v is not None:
            overrides[k] = int(v)
    return overrides

def save_overrides(overrides):
    for k in ('views', 'searches', 'images', 'videos', 'downloads'):
        if k in overrides and overrides[k] not in (None, ''):
            try:
                _store.set_metric(f'override_{k}', int(overrides[k]))
            except Exception:
                pass
        else:
            try:
                _store.delete_metric(f'override_{k}')
            except Exception:
                pass

def get_display(key, default=0):
    """Retorna valor real + bônus configurado (aditivo)."""
    overrides = get_overrides()
    real_val = _real.get(key, default) or 0
    bonus = overrides.get(key)
    if bonus is not None:
        try:
            return real_val + int(bonus)
        except (ValueError, TypeError):
            pass
    return real_val
