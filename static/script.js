// Initialize Bootstrap components and event listeners
document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // URL validation
    const urlInput = document.getElementById('url');
    const urlValidation = document.getElementById('urlValidation');
    const submitBtn = document.getElementById('submitBtn');

    if (urlInput) {
        urlInput.addEventListener('input', debounce(validateUrl, 500));
        urlInput.addEventListener('paste', function() {
            setTimeout(validateUrl, 100);
        });
    }

    // Form submission handling
    const scrapeForm = document.getElementById('scrapeForm');
    if (scrapeForm) {
        scrapeForm.addEventListener('submit', function(e) {
            showLoadingState();
        });
    }
});

// Debounce function to limit API calls
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// URL validation function
function validateUrl() {
    const urlInput = document.getElementById('url');
    const urlValidation = document.getElementById('urlValidation');
    
    if (!urlInput || !urlValidation) {
        return;
    }
    
    const url = urlInput.value.trim();

    if (!url) {
        urlValidation.innerHTML = '';
        return;
    }

    // Basic URL pattern validation
    const urlPattern = /^https?:\/\/.+\..+/;
    
    if (urlPattern.test(url)) {
        urlValidation.innerHTML = `
            <div class="text-success">
                <i class="fas fa-check-circle me-1"></i>
                URL válida
            </div>
        `;
    } else {
        urlValidation.innerHTML = `
            <div class="text-danger">
                <i class="fas fa-exclamation-circle me-1"></i>
                Formato de URL inválido. Use http:// ou https://
            </div>
        `;
    }
}

// Show loading state during form submission
function showLoadingState() {
    const submitBtn = document.getElementById('submitBtn');
    const submitText = document.getElementById('submitText');
    const loadingText = document.getElementById('loadingText');

    if (submitBtn && submitText && loadingText) {
        submitBtn.disabled = true;
        submitText.classList.add('d-none');
        loadingText.classList.remove('d-none');
    }
}

// Open image in modal
function openImageModal(imageUrl) {
    const modal = new bootstrap.Modal(document.getElementById('imageModal'));
    const modalImage = document.getElementById('modalImage');
    const modalDownload = document.getElementById('modalDownload');
    
    modalImage.src = imageUrl;
    modalDownload.href = imageUrl;
    
    modal.show();
}

// Copy URL to clipboard
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        showCopyToast();
    } catch (err) {
        // Fallback for older browsers
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        
        try {
            document.execCommand('copy');
            showCopyToast();
        } catch (err) {
            console.error('Falha ao copiar texto: ', err);
            showSiteNotice('Não foi possível copiar o link. Tente selecionar e copiar manualmente.', {
                type: 'error',
                title: 'Falha ao copiar'
            });
        } finally {
            document.body.removeChild(textArea);
        }
    }
}

// Show copy confirmation toast
function showCopyToast() {
    const toastElement = document.getElementById('copyToast');
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
}

// Lazy loading for images
function setupLazyLoading() {
    if ('IntersectionObserver' in window) {
        const imageObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    img.src = img.dataset.src;
                    img.classList.remove('lazy');
                    imageObserver.unobserve(img);
                }
            });
        });

        document.querySelectorAll('img[data-src]').forEach(img => {
            imageObserver.observe(img);
        });
    }
}

// Initialize lazy loading when DOM is ready
document.addEventListener('DOMContentLoaded', setupLazyLoading);

// Error handling for broken images
document.addEventListener('DOMContentLoaded', function() {
    const images = document.querySelectorAll('img');
    images.forEach(img => {
        img.addEventListener('error', function() {
            this.parentElement.innerHTML = `
                <div class="placeholder-image d-flex align-items-center justify-content-center">
                    <div class="text-center">
                        <i class="fas fa-image-slash fa-2x text-muted mb-2"></i>
                        <p class="text-muted small mb-0">Imagem não disponível</p>
                    </div>
                </div>
            `;
        });
    });
});

// ── Controle Global de Play Único Simultâneo ────────────────────────────────
// Sempre que qualquer <video> da página começar a tocar, pausa (sem resetar
// o buffer/timeline — apenas .pause()) todos os outros vídeos que estejam
// rodando. Usa delegação de evento na fase de captura em 'document', pois o
// evento 'play' não faz bubbling; funciona também para vídeos adicionados
// dinamicamente ao DOM (ex.: listagem de múltiplas URLs).
document.addEventListener('play', function(e) {
    const playedVideo = e.target;
    if (!playedVideo || playedVideo.tagName !== 'VIDEO') return;

    document.querySelectorAll('video').forEach(function(video) {
        if (video !== playedVideo && !video.paused) {
            video.pause();
        }
    });
}, true);

// Video error handling
document.addEventListener('DOMContentLoaded', function() {
    const videos = document.querySelectorAll('video');
    videos.forEach(video => {
        video.addEventListener('error', function() {
            this.parentElement.innerHTML = `
                <div class="text-center py-4">
                    <i class="fas fa-video-slash fa-3x text-muted mb-3"></i>
                    <p class="text-muted">Não foi possível carregar o vídeo</p>
                    <a href="${this.src}" target="_blank" class="btn btn-outline-primary btn-sm">
                        <i class="fas fa-external-link-alt me-2"></i>
                        Abrir Link Original
                    </a>
                </div>
            `;
        });
    });
});

// Auto-dismiss alerts after 5 seconds
document.addEventListener('DOMContentLoaded', function() {
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        if (!alert.querySelector('.btn-close')) return;
        
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
});

// Smooth scrolling for internal links
document.addEventListener('DOMContentLoaded', function() {
    const links = document.querySelectorAll('a[href^="#"]');
    links.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const targetId = this.getAttribute('href').substring(1);
            const targetElement = document.getElementById(targetId);
            
            if (targetElement) {
                targetElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl/Cmd + Enter to submit form
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        const form = document.getElementById('scrapeForm');
        if (form) {
            form.submit();
        }
    }
    
    // Escape to close modals
    if (e.key === 'Escape') {
        const openModals = document.querySelectorAll('.modal.show');
        openModals.forEach(modal => {
            const bsModal = bootstrap.Modal.getInstance(modal);
            if (bsModal) {
                bsModal.hide();
            }
        });
    }
});

// Progress bar for form submission (visual feedback)
function updateProgress(percent) {
    const progressBar = document.getElementById('progressBar');
    if (progressBar) {
        progressBar.style.width = percent + '%';
        progressBar.setAttribute('aria-valuenow', percent);
    }
}

// Simulate progress during scraping
function simulateProgress() {
    let progress = 0;
    const interval = setInterval(() => {
        progress += Math.random() * 15;
        if (progress >= 90) {
            progress = 90;
            clearInterval(interval);
        }
        updateProgress(progress);
    }, 500);
}

// Download all images
function downloadAllImages() {
    const images = document.querySelectorAll('#images img');
    const imageUrls = Array.from(images).map(img => img.src).filter(src => src && src !== '');
    
    if (imageUrls.length === 0) {
        showSiteNotice('Nenhuma imagem encontrada para download.', { type: 'info' });
        return;
    }
    
    downloadMultipleFiles(imageUrls, 'imagens');
}

// Download all videos
function downloadAllVideos() {
    const videoElements = document.querySelectorAll('#videos video source, #videos a[href*="mp4"], #videos a[href*="webm"], #videos a[href*="ogg"]');
    const videoUrls = Array.from(videoElements).map(element => {
        if (element.tagName === 'SOURCE') {
            return element.src;
        } else {
            return element.href;
        }
    }).filter(src => src && src !== '');
    
    // Also get direct video URLs from card text
    const videoCards = document.querySelectorAll('#videos .card-body small');
    videoCards.forEach(card => {
        const url = card.textContent.trim();
        if (url && (url.includes('.mp4') || url.includes('.webm') || url.includes('.ogg'))) {
            videoUrls.push(url);
        }
    });
    
    if (videoUrls.length === 0) {
        showSiteNotice('Nenhum vídeo encontrado para download.', { type: 'info' });
        return;
    }
    
    downloadMultipleFiles(videoUrls, 'vídeos');
}

// Download all media (images and videos)
function downloadAllMedia() {
    const images = document.querySelectorAll('#images img');
    const imageUrls = Array.from(images).map(img => img.src).filter(src => src && src !== '');
    
    const videoElements = document.querySelectorAll('#videos video source, #videos a[href*="mp4"], #videos a[href*="webm"], #videos a[href*="ogg"]');
    const videoUrls = Array.from(videoElements).map(element => {
        if (element.tagName === 'SOURCE') {
            return element.src;
        } else {
            return element.href;
        }
    }).filter(src => src && src !== '');
    
    // Also get direct video URLs from card text
    const videoCards = document.querySelectorAll('#videos .card-body small');
    videoCards.forEach(card => {
        const url = card.textContent.trim();
        if (url && (url.includes('.mp4') || url.includes('.webm') || url.includes('.ogg'))) {
            videoUrls.push(url);
        }
    });
    
    const allUrls = [...imageUrls, ...videoUrls];
    
    if (allUrls.length === 0) {
        showSiteNotice('Nenhum arquivo de mídia encontrado para download.', { type: 'info' });
        return;
    }
    
    downloadMultipleFiles(allUrls, 'arquivos de mídia');
}

// Helper function to download multiple files
function downloadMultipleFiles(urls, type) {
    if (!urls || urls.length === 0) {
        showSiteNotice(`Nenhum arquivo de ${type} encontrado.`, { type: 'info' });
        return;
    }
    
    const confirmed = confirm(`Deseja baixar ${urls.length} ${type}? Isso abrirá várias janelas de download.`);
    
    if (!confirmed) {
        return;
    }
    
    // Show progress toast
    showDownloadProgressToast(urls.length, type);
    
    let downloadCount = 0;
    const delay = 500; // Delay between downloads to avoid overwhelming the browser
    
    urls.forEach((url, index) => {
        setTimeout(() => {
            try {
                // Create a temporary link element
                const link = document.createElement('a');
                link.href = url;
                link.download = getFileNameFromUrl(url);
                link.target = '_blank';
                
                // Append to body, click, and remove
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                
                downloadCount++;
                updateDownloadProgress(downloadCount, urls.length);
                
                if (downloadCount === urls.length) {
                    setTimeout(() => {
                        hideDownloadProgressToast();
                        showCompletionToast(urls.length, type);
                    }, 1000);
                }
            } catch (error) {
                console.error('Erro ao baixar arquivo:', url, error);
            }
        }, index * delay);
    });
}

// Extract filename from URL
function getFileNameFromUrl(url) {
    try {
        const urlObj = new URL(url);
        const pathname = urlObj.pathname;
        const filename = pathname.split('/').pop();
        
        if (filename && filename.includes('.')) {
            return filename;
        }
        
        // Generate a filename based on the URL
        const extension = getFileExtension(url);
        const timestamp = Date.now();
        return `media_${timestamp}${extension}`;
    } catch (error) {
        // Fallback filename
        const timestamp = Date.now();
        const extension = getFileExtension(url);
        return `media_${timestamp}${extension}`;
    }
}

// Get file extension from URL
function getFileExtension(url) {
    const extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.mp4', '.webm', '.ogg', '.avi', '.mov'];
    
    for (const ext of extensions) {
        if (url.toLowerCase().includes(ext)) {
            return ext;
        }
    }
    
    return '.bin'; // Default extension
}

// Show download progress toast
function showDownloadProgressToast(total, type) {
    const toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) return;
    
    const progressToast = document.createElement('div');
    progressToast.id = 'downloadProgressToast';
    progressToast.className = 'toast show';
    progressToast.innerHTML = `
        <div class="toast-header">
            <i class="fas fa-download text-primary me-2"></i>
            <strong class="me-auto">Baixando ${type}</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast"></button>
        </div>
        <div class="toast-body">
            <div class="d-flex justify-content-between align-items-center mb-2">
                <span>Progresso:</span>
                <span id="downloadProgressText">0 / ${total}</span>
            </div>
            <div class="progress">
                <div id="downloadProgressBar" class="progress-bar" role="progressbar" style="width: 0%"></div>
            </div>
        </div>
    `;
    
    toastContainer.appendChild(progressToast);
}

// Update download progress
function updateDownloadProgress(current, total) {
    const progressText = document.getElementById('downloadProgressText');
    const progressBar = document.getElementById('downloadProgressBar');
    
    if (progressText && progressBar) {
        const percentage = Math.round((current / total) * 100);
        progressText.textContent = `${current} / ${total}`;
        progressBar.style.width = `${percentage}%`;
        progressBar.setAttribute('aria-valuenow', percentage);
    }
}

// Hide download progress toast
function hideDownloadProgressToast() {
    const progressToast = document.getElementById('downloadProgressToast');
    if (progressToast) {
        const bsToast = new bootstrap.Toast(progressToast);
        bsToast.hide();
        setTimeout(() => {
            progressToast.remove();
        }, 500);
    }
}

// Show completion toast
function showCompletionToast(count, type) {
    const toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) return;
    
    const completionToast = document.createElement('div');
    completionToast.className = 'toast';
    completionToast.innerHTML = `
        <div class="toast-header">
            <i class="fas fa-check-circle text-success me-2"></i>
            <strong class="me-auto">Download Concluído!</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast"></button>
        </div>
        <div class="toast-body">
            ${count} ${type} foram enviados para download.
        </div>
    `;
    
    toastContainer.appendChild(completionToast);
    const bsToast = new bootstrap.Toast(completionToast);
    bsToast.show();
    
    // Auto remove after some time
    setTimeout(() => {
        completionToast.remove();
    }, 5000);
}

