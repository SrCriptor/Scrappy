import logging
from datetime import datetime
from urllib.parse import urlparse

import admin_store as _store

logger = logging.getLogger("DebugLogger")


def log_failed_url(url: str, error: str, error_type: str = "unknown"):
    """Registra uma URL que falhou no download/acesso (persistido no banco)."""
    now = datetime.now().isoformat(timespec="seconds")
    try:
        domain = urlparse(url).netloc or "desconhecido"
    except Exception:
        domain = "desconhecido"

    try:
        _store.log_failed_url_db(url, error, error_type, domain, now)
    except Exception:
        pass

    # Formato compacto: uma linha por erro — sem tracebacks repetitivos
    short_url = url[:80] + '…' if len(url) > 80 else url
    short_err = str(error)[:60] + '…' if len(str(error)) > 60 else str(error)
    logger.warning(f"[{error_type.upper()}] {short_url} — {short_err}")


def mark_resolved(url: str):
    """Marca uma URL específica como resolvida."""
    try:
        return _store.mark_failed_url_resolved(url, True)
    except Exception:
        return False


def mark_domain_resolved(domain: str):
    """Marca todas as URLs de um domínio como resolvidas."""
    try:
        return _store.mark_failed_domain_resolved(domain)
    except Exception:
        return 0


def delete_url(url: str):
    """Remove permanentemente uma URL específica do log (DELETE explícito)."""
    try:
        return _store.delete_failed_url(url)
    except Exception:
        return False


def unmark_resolved(url: str):
    """Desfaz o 'resolvido' de uma URL específica, voltando para não resolvida."""
    try:
        return _store.mark_failed_url_resolved(url, False)
    except Exception:
        return False


def delete_domain(domain: str) -> int:
    """Remove permanentemente TODAS as URLs de um domínio específico do log (DELETE explícito).
    Retorna o número de entradas excluídas."""
    try:
        return _store.delete_failed_domain(domain)
    except Exception:
        return 0


def delete_resolved():
    """Remove permanentemente todas as entradas marcadas como resolvidas."""
    try:
        return _store.delete_resolved_failed_urls()
    except Exception:
        return 0


def clear_all():
    """Apaga todo o log de debug."""
    try:
        _store.clear_all_failed_urls()
    except Exception:
        pass


def get_summary() -> dict:
    """Retorna resumo do log para exibição."""
    try:
        entries = _store.get_failed_urls()
    except Exception:
        entries = []

    unresolved = [e for e in entries if not e.get("resolved")]
    resolved = [e for e in entries if e.get("resolved")]

    domains = {}
    for e in unresolved:
        d = e.get("domain", "desconhecido")
        if d not in domains:
            domains[d] = {"domain": d, "count": 0, "urls": []}
        domains[d]["count"] += e.get("count", 1)
        domains[d]["urls"].append(e)

    return {
        "total": len(entries),
        "unresolved": len(unresolved),
        "resolved": len(resolved),
        "domains": sorted(domains.values(), key=lambda x: x["count"], reverse=True),
        "all_entries": sorted(entries, key=lambda x: x.get("last_seen", ""), reverse=True),
    }
