import requests
import re
import threading
import time as _time
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import logging
import colorlog

# Configurar logger para o scraper
scraper_logger = logging.getLogger('MediaScraper')

class MediaScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Extensões de mídia suportadas
        self.image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
        self.video_extensions = {'.mp4', '.webm', '.ogg', '.flv', '.avi', '.mov', '.wmv', '.mkv'}
        
        # Padrões regex para encontrar URLs de mídia genéricas
        self.media_patterns = [
            r'https?://[^\s"\'<>]+\.(?:mp4|webm|ogg|png|jpg|jpeg|gif|webp|bmp|flv|avi|mov|wmv|mkv)',
            r'https?://[^\s"\'<>]*(?:youtube\.com/watch\?v=|youtu\.be/|vimeo\.com/)[^\s"\'<>]*',
            r'https?://[^\s"\'<>]*(?:instagram\.com/p/|twitter\.com/[^/]+/status/)[^\s"\'<>]*'
        ]

    def scrape_media(self, url):
        """
        Faz scraping de uma URL e extrai todos os links de mídia.
        Intercepta URLs do XVideos e Pornhub para processamento dedicado.
        """
        # ── INTERCEPTADOR DEDICADO PARA O XVIDEOS ──
        if 'xvideos.com' in url.lower():
            return self._scrape_xvideos(url)
            
        # ── INTERCEPTADOR DEDICADO PARA O PORNHUB ──
        if 'pornhub.com' in url.lower():
            return self._scrape_pornhub(url)
        # ───────────────────────────────────────────

        try:
            # Fazer requisição para a página
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extrair mídia de diferentes fontes
            images = self._extract_images(soup, url)
            videos = self._extract_videos(soup, url)
            
            # Extrair mídia adicional usando regex no HTML
            html_content = response.text
            regex_media = self._extract_with_regex(html_content, url)
            
            # Combinar e remover duplicatas
            all_images = list(set(images + regex_media['images']))
            all_videos = list(set(videos + regex_media['videos']))
            
            # Filtrar e validar URLs
            valid_images = self._validate_media_urls(all_images)
            valid_videos = self._validate_media_urls(all_videos)
            
            return {
                'images': valid_images,
                'videos': valid_videos,
                'total_media': len(valid_images) + len(valid_videos)
            }
            
        except requests.RequestException as e:
            # ── Silent re-route: keep container console clean, persist to debug log ──
            _failed_url  = url
            _err_type    = type(e).__name__   # e.g. "ConnectTimeout", "ReadTimeout"
            _err_detail  = str(e)
            _ts          = _time.strftime('%Y-%m-%dT%H:%M:%SZ', _time.gmtime())
            def _silent_network_log(fu=_failed_url, et=_err_type, ed=_err_detail, ts=_ts):
                try:
                    import debug_logger as _dl
                    _dl.log_failed_url(fu, f"{et}: {ed}", "Timeout/Block")
                except Exception:
                    pass
            threading.Thread(target=_silent_network_log, daemon=True).start()
            raise Exception(f"Não foi possível acessar a página: {_err_detail}")
        except Exception as e:
            scraper_logger.error(f"⚠️ Erro no scraping: {e}")
            raise Exception(f"Erro ao processar a página: {str(e)}")

    def _scrape_xvideos(self, url):
        """
        Método isolado para extrair os links de vídeo reais contidos nos
        scripts JavaScript internos do player do XVideos.
        """
        try:
            # Cabeçalhos necessários para bypass de verificação básica
            headers = self.session.headers.copy()
            headers.update({
                'Referer': 'https://xvideos.com',
                'Cookie': 'verified=1'
            })
            
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            html = response.text
            
            raw_videos = []
            
            # 1. Captura a URL do vídeo em Alta Definição (setVideoUrlHigh)
            match_high = re.search(r"html5player\.setVideoUrlHigh\('([^']+)'\)", html)
            if match_high:
                raw_videos.append(match_high.group(1))
                
            # 2. Captura a URL do vídeo em Baixa Definição (setVideoUrlLow)
            match_low = re.search(r"html5player\.setVideoUrlLow\('([^']+)'\)", html)
            if match_low:
                raw_videos.append(match_low.group(1))
                
            # 3. Captura o link de streaming adaptativo HLS (.m3u8)
            match_hls = re.search(r"html5player\.setVideoHLS\('([^']+)'\)", html)
            if match_hls:
                raw_videos.append(match_hls.group(1))

            # Extrair a capa do vídeo (thumbnail) via Open Graph
            soup = BeautifulSoup(html, 'html.parser')
            raw_images = []
            meta_thumb = soup.find('meta', property='og:image')
            if meta_thumb and meta_thumb.get('content'):
                raw_images.append(meta_thumb.get('content'))

            # Valida e limpa as listas geradas usando os validadores da classe
            valid_images = self._validate_media_urls(list(set(raw_images)))
            valid_videos = self._validate_media_urls(list(set(raw_videos)))

            return {
                'images': valid_images,
                'videos': valid_videos,
                'total_media': len(valid_images) + len(valid_videos)
            }
            
        except Exception as e:
            scraper_logger.error(f"⚠️ Erro no scraping do XVideos: {e}")
            raise Exception(f"Erro ao processar página do XVideos: {str(e)}")

    def _scrape_pornhub(self, url):
        """
        Método dedicado que extrai as streams de vídeo fragmentadas do Pornhub
        usando o motor do yt-dlp para descriptografar os parâmetros de rede.
        """
        try:
            import yt_dlp
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'format': 'best',
                'skip_download': True,
                'allowed_extractors': ['pornhub'],
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                videos = []
                images = []
                
                if info.get('url'):
                    videos.append(info['url'])
                
                if info.get('formats'):
                    for f in info['formats']:
                        if f.get('url') and ('.mp4' in f['url'] or '.m3u8' in f['url'] or 'video' in f.get('vcodec', '')):
                            if f['url'] not in videos:
                                videos.append(f['url'])
                                
                if info.get('thumbnail'):
                    images.append(info['thumbnail'])
                
                valid_images = self._validate_media_urls(images)
                valid_videos = self._validate_media_urls(videos)
                
                return {
                    'images': valid_images,
                    'videos': valid_videos,
                    'total_media': len(valid_images) + len(valid_videos)
                }
                
        except Exception as e:
            scraper_logger.error(f"⚠️ Erro no scraping do Pornhub via yt-dlp: {e}")
            raise Exception(f"Erro ao descriptografar streams do Pornhub: {str(e)}")

    def _extract_images(self, soup, base_url):
        """Extrai imagens de tags HTML tradicionais"""
        images = []
        
        # Tags img
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                full_url = urljoin(base_url, src)
                if self._is_image_url(full_url):
                    images.append(full_url)
        
        # Tags picture e source
        for picture in soup.find_all('picture'):
            for source in picture.find_all('source'):
                srcset = source.get('srcset')
                if srcset:
                    urls = re.findall(r'(https?://[^\s,]+)', srcset)
                    for url in urls:
                        full_url = urljoin(base_url, url)
                        if self._is_image_url(full_url):
                            images.append(full_url)
        
        # Meta tags (Open Graph, Twitter Cards)
        for meta in soup.find_all('meta'):
            property_val = meta.get('property') or meta.get('name')
            if property_val in ['og:image', 'twitter:image', 'twitter:image:src']:
                content = meta.get('content')
                if content:
                    full_url = urljoin(base_url, content)
                    if self._is_image_url(full_url):
                        images.append(full_url)
        
        return images

    def _extract_videos(self, soup, base_url):
        """Extrai vídeos de tags HTML estruturadas"""
        videos = []

        # Tags video
        for video in soup.find_all('video'):
            src = video.get('src')
            if src:
                full_url = urljoin(base_url, src)
                if self._is_video_url(full_url):
                    videos.append(full_url)
            # Sources dentro do video
            for source in video.find_all('source'):
                src = source.get('src')
                if src:
                    full_url = urljoin(base_url, src)
                    if self._is_video_url(full_url):
                        videos.append(full_url)
        
        # iframes (YouTube, Vimeo, etc.)
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if src and ('youtube.com' in src or 'vimeo.com' in src or 'dailymotion.com' in src):
                full_url = urljoin(base_url, src)
                videos.append(full_url)
        
        # Meta tags para vídeos
        for meta in soup.find_all('meta'):
            property_val = meta.get('property') or meta.get('name')
            if property_val in ['og:video', 'og:video:url', 'twitter:player']:
                content = meta.get('content')
                if content:
                    full_url = urljoin(base_url, content)
                    videos.append(full_url)
        
        return videos

    def _extract_with_regex(self, html_content, base_url):
        """Extrai mídia injetada usando regex no conteúdo HTML puro"""
        images = []
        videos = []
        
        for pattern in self.media_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for match in matches:
                full_url = urljoin(base_url, match)
                if self._is_image_url(full_url):
                    images.append(full_url)
                elif self._is_video_url(full_url) or 'youtube.com' in full_url or 'vimeo.com' in full_url:
                    videos.append(full_url)
        
        return {'images': images, 'videos': videos}

    def _is_image_url(self, url):
        """Verifica se a URL é de uma imagem com base na extensão"""
        try:
            parsed = urlparse(url.lower())
            path = parsed.path
            return any(path.endswith(ext) for ext in self.image_extensions)
        except:
            return False

    def _is_video_url(self, url):
        """Verifica se a URL é de um vídeo com base na extensão"""
        try:
            parsed = urlparse(url.lower())
            path = parsed.path
            return any(path.endswith(ext) for ext in self.video_extensions)
        except:
            return False

    def _validate_media_urls(self, urls):
        """Valida, estrutura e filtra URLs de mídia protegendo tokens dinâmicos"""
        valid_urls = []
        
        for url in urls:
            try:
                parsed = urlparse(url)
                if parsed.scheme in ['http', 'https'] and parsed.netloc:
                    # Preserva os parâmetros completos para links do XVideos e Pornhub/CDNs
                    if any(domain in url.lower() for domain in ['xvideos', 'xv-video', 'pornhub', 'phncdn']):
                        clean_url = url
                    else:
                        # Para outros sites, remove as queries mantendo apenas o link limpo
                        clean_url = url.split('?')[0] if not ('youtube.com' in url or 'vimeo.com' in url) else url
                    
                    valid_urls.append(clean_url)
            except:
                continue
        
        return valid_urls
