# Scraper de Mídia Web

Ferramenta Flask para extrair e baixar imagens/vídeos de páginas web.

## Arquitetura

- `app.py` — Servidor Flask, rotas principais e de debug
- `media_scraper.py` — Lógica de scraping (BeautifulSoup + regex)
- `debug_logger.py` — Sistema de log de URLs com falha (JSON)
- `failed_urls.json` — Gerado automaticamente; armazena URLs que falharam no download
- `templates/` — index.html, results.html, debug.html
- `static/` — style.css, script.js

## Rotas

| Rota | Descrição |
|------|-----------|
| `/` | Página principal |
| `/scrape` | POST — faz scraping de uma URL |
| `/force-download` | GET — baixa um arquivo via proxy |
| `/debug` | Painel de debug de URLs falhas |
| `/debug/json` | JSON bruto do log de debug |
| `/debug/resolve` | POST — marca URL como resolvida |
| `/debug/resolve_domain` | POST — marca domínio inteiro como resolvido |
| `/debug/clean` | POST — remove entradas resolvidas |
| `/debug/clear` | POST — apaga todo o log |
| `/ping` | Health check |

## Sistema de Debug

O `debug_logger.py` registra URLs que falharam no download em `failed_urls.json`:
- Sem dados de cliente (sem IP, sem user-agent, sem sessão)
- Agrupado por domínio
- Contador de ocorrências por URL
- Timestamp de primeira e última ocorrência
- Tipo de erro (http_error, connection_error, timeout, request_error)
- Suporte a marcar como resolvido individualmente ou por domínio
- Endpoint `/debug/json` para integração externa

## Dependências

Flask, requests, beautifulsoup4, colorlog

## Porta

5000 (mapeada para 3000 externamente)
