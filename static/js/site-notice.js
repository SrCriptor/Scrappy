/* Consistent, non-blocking notices for the public site and admin screens.
   This replaces the browser's native alert box, which ignores the app theme
   and interrupts the user's current action. */
(function (window, document) {
    'use strict';

    var ICONS = {
        info: 'i',
        success: '✓',
        warning: '!',
        error: '×'
    };
    var TITLES = {
        info: 'Informação',
        success: 'Tudo certo',
        warning: 'Atenção',
        error: 'Não foi possível concluir'
    };
    var noticeCounter = 0;

    function ensureStyles() {
        if (document.getElementById('siteNoticeStyles')) return;
        var style = document.createElement('style');
        style.id = 'siteNoticeStyles';
        style.textContent = [
            '.site-notice-stack{position:fixed;top:1.25rem;right:1.25rem;z-index:30000;width:min(380px,calc(100vw - 2rem));display:flex;flex-direction:column;gap:.75rem;pointer-events:none;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}',
            '.site-notice{--notice-color:#38bdf8;--notice-glow:rgba(56,189,248,.22);position:relative;display:grid;grid-template-columns:2.35rem minmax(0,1fr) 1.5rem;align-items:start;gap:.75rem;padding:.9rem 1rem .95rem .85rem;background:linear-gradient(145deg,rgba(22,27,34,.98),rgba(13,17,23,.98));border:1px solid #30363d;border-left:3px solid var(--notice-color);border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.5),0 0 22px var(--notice-glow);color:#e6edf3;pointer-events:auto;overflow:hidden;opacity:0;transform:translate3d(24px,-8px,0) scale(.97);transition:opacity .22s ease,transform .22s ease;}',
            '.site-notice.is-visible{opacity:1;transform:translate3d(0,0,0) scale(1);}',
            '.site-notice.is-leaving{opacity:0;transform:translate3d(24px,-8px,0) scale(.97);}',
            '.site-notice[data-type="success"]{--notice-color:#00ff88;--notice-glow:rgba(0,255,136,.2);}',
            '.site-notice[data-type="warning"]{--notice-color:#fbbf24;--notice-glow:rgba(251,191,36,.2);}',
            '.site-notice[data-type="error"]{--notice-color:#fb7185;--notice-glow:rgba(251,113,133,.2);}',
            '.site-notice-icon{width:2.1rem;height:2.1rem;display:grid;place-items:center;border:1px solid var(--notice-color);border-radius:50%;color:var(--notice-color);font-size:1.15rem;font-weight:800;line-height:1;box-shadow:0 0 12px var(--notice-glow);}',
            '.site-notice-copy{min-width:0;padding-top:.05rem;}',
            '.site-notice-title{margin:0 0 .2rem;color:#f8fafc;font-size:.78rem;font-weight:800;letter-spacing:.03em;}',
            '.site-notice-message{margin:0;color:#b8c2cc;font-size:.78rem;line-height:1.42;overflow-wrap:anywhere;}',
            '.site-notice-close{width:1.5rem;height:1.5rem;padding:0;border:0;background:transparent;color:#8b949e;font-size:1.15rem;line-height:1;cursor:pointer;transition:color .15s ease,transform .15s ease;}',
            '.site-notice-close:hover{color:#fff;transform:scale(1.12);}',
            '.site-notice-progress{position:absolute;right:0;bottom:0;left:0;height:2px;background:var(--notice-color);box-shadow:0 0 8px var(--notice-color);transform-origin:left;animation:siteNoticeProgress var(--notice-duration,4.2s) linear forwards;}',
            '@keyframes siteNoticeProgress{from{transform:scaleX(1)}to{transform:scaleX(0)}}',
            '@media (max-width:520px){.site-notice-stack{top:.75rem;right:.75rem;width:calc(100vw - 1.5rem);}.site-notice{padding:.8rem .85rem .85rem .75rem;}}',
            '@media (prefers-reduced-motion:reduce){.site-notice{transition:opacity .15s ease;transform:none;}.site-notice.is-leaving{transform:none;}.site-notice-progress{animation:none;opacity:.45;}}'
        ].join('');
        (document.head || document.documentElement).appendChild(style);
    }

    function ensureStack() {
        var stack = document.getElementById('siteNoticeStack');
        if (stack) return stack;
        stack = document.createElement('div');
        stack.id = 'siteNoticeStack';
        stack.className = 'site-notice-stack';
        stack.setAttribute('aria-live', 'polite');
        stack.setAttribute('aria-atomic', 'false');
        (document.body || document.documentElement).appendChild(stack);
        return stack;
    }

    function closeNotice(notice) {
        if (!notice || notice.classList.contains('is-leaving')) return;
        notice.classList.remove('is-visible');
        notice.classList.add('is-leaving');
        window.setTimeout(function () {
            if (notice.parentNode) notice.parentNode.removeChild(notice);
        }, 240);
    }

    window.showSiteNotice = function (message, options) {
        options = options || {};
        ensureStyles();

        var type = ['info', 'success', 'warning', 'error'].indexOf(options.type) !== -1
            ? options.type : 'info';
        var duration = Number(options.duration);
        if (!isFinite(duration) || duration <= 0) duration = 4200;

        var notice = document.createElement('div');
        noticeCounter += 1;
        notice.id = 'siteNotice' + noticeCounter;
        notice.className = 'site-notice';
        notice.dataset.type = type;
        notice.style.setProperty('--notice-duration', duration + 'ms');
        notice.setAttribute('role', 'alert');

        var icon = document.createElement('span');
        icon.className = 'site-notice-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = options.icon || ICONS[type];

        var copy = document.createElement('div');
        copy.className = 'site-notice-copy';
        var title = document.createElement('p');
        title.className = 'site-notice-title';
        title.textContent = options.title || TITLES[type];
        var text = document.createElement('p');
        text.className = 'site-notice-message';
        text.textContent = String(message || '');
        copy.appendChild(title);
        copy.appendChild(text);

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'site-notice-close';
        close.setAttribute('aria-label', 'Fechar aviso');
        close.textContent = '×';
        close.addEventListener('click', function () { closeNotice(notice); });

        var progress = document.createElement('span');
        progress.className = 'site-notice-progress';
        progress.setAttribute('aria-hidden', 'true');

        notice.appendChild(icon);
        notice.appendChild(copy);
        notice.appendChild(close);
        notice.appendChild(progress);
        ensureStack().appendChild(notice);

        window.requestAnimationFrame(function () {
            notice.classList.add('is-visible');
        });
        window.setTimeout(function () { closeNotice(notice); }, duration);
        return notice;
    };
})(window, document);