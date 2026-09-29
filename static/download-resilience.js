/* Individual media download tracking and network-aware recovery. */
(function (window, document) {
  'use strict';

  var states = Object.create(null);
  var activeRun = null;
  var paused = false;
  var pauseWaiters = [];
  var PERMANENT_STATUS = { 403: true, 404: true, 410: true };

  function normalizeUrl(url) {
    var value = String(url || '').trim();
    if (value && !/^https?:\/\//i.test(value)) value = 'https://' + value;
    return value;
  }

  function setState(url, state) {
    var normalized = normalizeUrl(url);
    states[normalized] = state;

    document.querySelectorAll('[data-media-url]').forEach(function (card) {
      if (normalizeUrl(card.getAttribute('data-media-url')) !== normalized) return;
      card.dataset.downloadState = state;
      card.classList.remove(
        'download-state-sucesso',
        'download-state-falha-conexao',
        'download-state-indisponivel'
      );
      card.classList.add('download-state-' + state);
    });

    window.dispatchEvent(new CustomEvent('media-download-state', {
      detail: { url: normalized, state: state }
    }));
  }

  function getState(url) {
    return states[normalizeUrl(url)] || null;
  }

  function reset() {
    states = Object.create(null);
    document.querySelectorAll('[data-media-url]').forEach(function (card) {
      delete card.dataset.downloadState;
      card.classList.remove(
        'download-state-sucesso',
        'download-state-falha_conexao',
        'download-state-indisponivel'
      );
    });
  }

  function mark(url, state) {
    if (state === 'sucesso' || state === 'falha_conexao' || state === 'indisponivel') {
      setState(url, state);
    }
  }

  function filenameFromResponse(response, sourceUrl) {
    var disposition = response.headers.get('Content-Disposition') || '';
    var match = disposition.match(/filename="([^"]+)"/i) ||
      disposition.match(/filename=([^;]+)/i);
    if (match && match[1]) return match[1].trim();
    try {
      var path = new URL(sourceUrl).pathname;
      var name = path.split('/').pop();
      return name || 'download';
    } catch (_) {
      return 'download';
    }
  }

  function downloadBlob(blob, filename) {
    var objectUrl = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename || 'download';
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 1000);
  }

  function displayNameFromUrl(url) {
    try {
      var path = new URL(url).pathname;
      var name = decodeURIComponent(path.split('/').pop() || '');
      return name || 'arquivo atual';
    } catch (_) {
      return 'arquivo atual';
    }
  }

  function notifyProgress(options, detail) {
    var eventDetail = Object.assign({}, detail || {});
    if (eventDetail.paused === undefined) eventDetail.paused = paused;
    if (!options || options.broadcastProgress !== false) {
      window.dispatchEvent(new CustomEvent('media-download-progress', {
        detail: eventDetail
      }));
    }
    if (options && typeof options.onProgress === 'function') {
      options.onProgress(eventDetail);
    }
  }

  function waitWhilePaused() {
    if (!paused) return Promise.resolve();
    return new Promise(function (resolve) {
      pauseWaiters.push(resolve);
    });
  }

  function setPaused(value) {
    var next = value === true;
    if (paused === next) return paused;
    paused = next;
    if (!paused) {
      var waiters = pauseWaiters.splice(0);
      waiters.forEach(function (resolve) { resolve(); });
    }
    window.dispatchEvent(new CustomEvent('media-download-pause', {
      detail: { paused: paused }
    }));
    return paused;
  }

  async function consumeDownload(url, options) {
    options = options || {};
    var normalized = normalizeUrl(url);
    var fileName = displayNameFromUrl(normalized);
    notifyProgress(options, {
      phase: 'file-start',
      url: normalized,
      fileName: fileName,
      received: 0,
      expected: 0,
      filePercent: null
    });
    await waitWhilePaused();
    if (/(copyright|dmca|removed|deleted|excluido|excluído)/i.test(normalized)) {
      setState(normalized, 'indisponivel');
      notifyProgress(options, {
        phase: 'file-complete',
        url: normalized,
        fileName: fileName,
        received: 0,
        expected: 0,
        filePercent: 100,
        state: 'indisponivel'
      });
      return 'indisponivel';
    }
    try {
      var response = await fetch('/force-download?url=' + encodeURIComponent(normalized), {
        cache: 'no-store'
      });

      if (PERMANENT_STATUS[response.status]) {
        setState(normalized, 'indisponivel');
        notifyProgress(options, {
          phase: 'file-complete',
          url: normalized,
          fileName: fileName,
          received: 0,
          expected: 0,
          filePercent: 100,
          state: 'indisponivel'
        });
        return 'indisponivel';
      }
      if (!response.ok) {
        setState(normalized, 'falha_conexao');
        notifyProgress(options, {
          phase: 'file-complete',
          url: normalized,
          fileName: fileName,
          received: 0,
          expected: 0,
          filePercent: null,
          state: 'falha_conexao'
        });
        return 'falha_conexao';
      }

      fileName = filenameFromResponse(response, normalized);
      var expected = Number(response.headers.get('X-Download-Expected-Length') ||
        response.headers.get('Content-Length') || 0);
      notifyProgress(options, {
        phase: 'file-progress',
        url: normalized,
        fileName: fileName,
        received: 0,
        expected: expected,
        filePercent: expected > 0 ? 0 : null
      });

      var reader = response.body && response.body.getReader
        ? response.body.getReader()
        : null;
      if (!reader) {
        await waitWhilePaused();
        var fallbackBlob = await response.blob();
        downloadBlob(fallbackBlob, fileName);
        setState(normalized, 'sucesso');
        notifyProgress(options, {
          phase: 'file-complete',
          url: normalized,
          fileName: fileName,
          received: fallbackBlob.size,
          expected: expected || fallbackBlob.size,
          filePercent: 100,
          state: 'sucesso'
        });
        return 'sucesso';
      }

      var chunks = [];
      var received = 0;
      while (true) {
        var part = await reader.read();
        if (part.done) break;
        if (part.value && part.value.byteLength) {
          chunks.push(part.value);
          received += part.value.byteLength;
          notifyProgress(options, {
            phase: 'file-progress',
            url: normalized,
            fileName: fileName,
            received: received,
            expected: expected,
            filePercent: expected > 0
              ? Math.min(100, Math.round(received / expected * 100))
              : null
          });
        }
        await waitWhilePaused();
      }

      /* The backend forwards this when the origin provides a length. It lets
         the browser distinguish a clean EOF from a truncated origin stream. */
      if (expected > 0 && received < expected) {
        setState(normalized, 'falha_conexao');
        notifyProgress(options, {
          phase: 'file-complete',
          url: normalized,
          fileName: fileName,
          received: received,
          expected: expected,
          filePercent: Math.min(100, Math.round(received / expected * 100)),
          state: 'falha_conexao'
        });
        return 'falha_conexao';
      }

      var blob = new Blob(chunks, {
        type: response.headers.get('Content-Type') || 'application/octet-stream'
      });
      downloadBlob(blob, fileName);
      setState(normalized, 'sucesso');
      notifyProgress(options, {
        phase: 'file-complete',
        url: normalized,
        fileName: fileName,
        received: received,
        expected: expected || received,
        filePercent: 100,
        state: 'sucesso'
      });
      return 'sucesso';
    } catch (_) {
      setState(normalized, 'falha_conexao');
      notifyProgress(options, {
        phase: 'file-complete',
        url: normalized,
        fileName: fileName,
        received: 0,
        expected: 0,
        filePercent: null,
        state: 'falha_conexao'
      });
      return 'falha_conexao';
    }
  }

  function failedUrls() {
    return Object.keys(states).filter(function (url) {
      return states[url] === 'falha_conexao';
    });
  }

  async function copyFailedUrls(urls) {
    var text = (urls || failedUrls()).map(normalizeUrl).join('\n');
    if (!text) return false;
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      return false;
    }
  }

  function showRecoveryModal(urls) {
    if (!urls.length || typeof window.Swal === 'undefined') return Promise.resolve(false);

    return window.Swal.fire({
      icon: 'warning',
      title: 'Processamento concluído',
      text: 'O processamento terminou. ' + urls.length +
        ' mídias falharam devido a erros de rede. Deseja tentar baixar novamente apenas as mídias que falharam?',
      showCancelButton: true,
      showDenyButton: true,
      confirmButtonText: 'Tentar Novamente',
      denyButtonText: '📋 Copiar falhas',
      cancelButtonText: 'Fechar',
      reverseButtons: true,
      allowOutsideClick: false
    }).then(async function (result) {
      if (result.isDenied) {
        var copied = await copyFailedUrls(urls);
        if (typeof window.showSiteNotice === 'function') {
          window.showSiteNotice(
            copied ? 'As URLs das falhas foram copiadas para a área de transferência.'
              : 'Não foi possível copiar as URLs.',
            {
              type: copied ? 'success' : 'error',
              title: copied ? 'Falhas copiadas' : 'Falha ao copiar'
            }
          );
        }
        return false;
      }
      if (result.isConfirmed) return true;
      return false;
    });
  }

  async function processQueue(queue, options, retryOnly) {
      var total = queue.length;
      var completed = 0;
      var concurrency = Number(options.concurrency);
      if (!Number.isFinite(concurrency) || concurrency < 1) concurrency = 5;
      concurrency = Math.max(1, Math.min(5, Math.floor(concurrency)));
      var cursor = 0;

      async function worker() {
        while (true) {
          await waitWhilePaused();
          var index = cursor++;
          if (index >= total) return;
          var currentUrl = queue[index];
          await consumeDownload(currentUrl, {
            broadcastProgress: false,
            onProgress: function (detail) {
              notifyProgress(options, Object.assign({}, detail, {
                completed: completed,
                total: total,
                index: index,
                paused: paused
              }));
            }
          });
          completed += 1;
          notifyProgress(options, {
            phase: 'queue-progress',
            url: currentUrl,
            fileName: displayNameFromUrl(currentUrl),
            completed: completed,
            total: total,
            index: index,
            state: getState(currentUrl),
            filePercent: 100,
            paused: paused
          });
        }
      }

      var workers = [];
      for (var workerIndex = 0; workerIndex < Math.min(concurrency, total); workerIndex += 1) {
        workers.push(worker());
      }
      await Promise.all(workers);

      var failed = queue.filter(function (url) { return getState(url) === 'falha_conexao'; });
      var unavailable = queue.filter(function (url) { return getState(url) === 'indisponivel'; });
      var success = queue.filter(function (url) { return getState(url) === 'sucesso'; });

      if (failed.length && !retryOnly && options.showRecovery !== false) {
        var retry = await showRecoveryModal(failed);
        if (retry) {
          await processQueue(failed, options, true);
          failed = queue.filter(function (url) { return getState(url) === 'falha_conexao'; });
          unavailable = queue.filter(function (url) { return getState(url) === 'indisponivel'; });
          success = queue.filter(function (url) { return getState(url) === 'sucesso'; });
        }
      }

      return { success: success, failed: failed, unavailable: unavailable };
  }

  async function downloadMany(inputUrls, options) {
    options = options || {};
    var urls = (inputUrls || []).map(normalizeUrl).filter(Boolean);
    var retryOnly = options.retryOnly === true;
    var queue = urls.filter(function (url, index) {
      if (urls.indexOf(url) !== index) return false;
      var current = getState(url);
      if (current === 'sucesso' || current === 'indisponivel') return false;
      return !retryOnly || current === 'falha_conexao';
    });

    if (!queue.length) return { success: [], failed: [], unavailable: [] };
    if (activeRun) return activeRun;

    activeRun = processQueue(queue, options, retryOnly);

    try {
      return await activeRun;
    } finally {
      activeRun = null;
      setPaused(false);
    }
  }

  async function downloadOne(url, options) {
    options = options || {};
    if (activeRun) return activeRun;
    var normalized = normalizeUrl(url);
    var singleOptions = Object.assign({}, options, {
      broadcastProgress: false,
      onProgress: function (detail) {
        notifyProgress(options, Object.assign({}, detail, {
          completed: detail.phase === 'file-complete' ? 1 : 0,
          total: 1,
          index: 0,
          paused: paused
        }));
      }
    });
    activeRun = consumeDownload(normalized, singleOptions).then(function (state) {
      return state;
    });
    try {
      return await activeRun;
    } finally {
      activeRun = null;
      setPaused(false);
    }
  }

  window.MediaDownloadManager = {
    downloadMany: downloadMany,
    downloadOne: downloadOne,
    copyFailedUrls: copyFailedUrls,
    failedUrls: failedUrls,
    getState: getState,
    pause: function () {
      if (!activeRun) return false;
      return setPaused(true);
    },
    resume: function () {
      return setPaused(false);
    },
    isPaused: function () { return paused; },
    reset: reset,
    mark: mark
  };
})(window, document);