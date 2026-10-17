/* Stable per-file progress rows for concurrent downloads. */
(function (window, document) {
  'use strict';

  var MAX_VISIBLE_FILES = 5;

  function resolveContainer(target) {
    return typeof target === 'string' ? document.getElementById(target) : target;
  }

  function getState(container) {
    if (!container) return null;
    if (!container._downloadProgressListState) {
      container._downloadProgressListState = {
        rows: Object.create(null),
        order: [],
        percentages: Object.create(null),
        total: null
      };
    }
    return container._downloadProgressListState;
  }

  function removeRow(container, state, key) {
    var row = state.rows[key];
    if (!row) return;
    if (row._removeTimer) window.clearTimeout(row._removeTimer);
    row.remove();
    delete state.rows[key];
    state.order = state.order.filter(function (item) { return item !== key; });
  }

  function reset(target) {
    var container = resolveContainer(target);
    if (!container) return;
    var state = getState(container);
    state.order.slice().forEach(function (key) {
      removeRow(container, state, key);
    });
    state.percentages = Object.create(null);
    state.total = null;
    container.innerHTML = '';
  }

  function makeRow(container, state, key) {
    while (container.children.length >= MAX_VISIBLE_FILES) {
      var settledKey = state.order.find(function (item) {
        var row = state.rows[item];
        return row && row.dataset.state !== 'active';
      });
      if (settledKey) {
        removeRow(container, state, settledKey);
      } else {
        return null;
      }
    }

    var row = document.createElement('div');
    row.className = 'download-progress-file';
    row.dataset.state = 'active';
    row.innerHTML =
      '<div class="download-progress-file__head">' +
        '<span class="download-progress-file__name"></span>' +
        '<span class="download-progress-file__percent">—</span>' +
      '</div>' +
      '<div class="download-progress-file__track">' +
        '<div class="download-progress-file__fill" role="progressbar" ' +
          'aria-valuemin="0" aria-valuemax="100"></div>' +
      '</div>';
    row._name = row.querySelector('.download-progress-file__name');
    row._percent = row.querySelector('.download-progress-file__percent');
    row._fill = row.querySelector('.download-progress-file__fill');
    row._normalStatus = 'Iniciando…';
    container.appendChild(row);
    state.rows[key] = row;
    state.order.push(key);
    return row;
  }

  function setRowProgress(row, progress, paused) {
    var phase = progress.phase;
    var value = progress.filePercent;
    var hasPercent = value !== null && value !== undefined && Number.isFinite(Number(value));
    var percent = hasPercent
      ? Math.max(0, Math.min(100, Math.round(Number(value))))
      : null;

    if (progress.fileName) row._name.textContent = progress.fileName;
    row._name.title = row._name.textContent;
    row._fill.classList.toggle('is-indeterminate', !hasPercent);

    if (phase === 'file-complete') {
      var succeeded = progress.state === 'sucesso';
      row.dataset.state = succeeded ? 'complete' : 'failed';
      row._normalStatus = succeeded
        ? 'Concluído · 100%'
        : (progress.state === 'indisponivel'
          ? 'Indisponível'
          : (percent === null ? 'Falha no download' : 'Falha · ' + percent + '%'));
      row._percent.textContent = row._normalStatus;
      percent = succeeded ? 100 : percent;
      row._fill.classList.remove('is-indeterminate');
    } else {
      row.dataset.state = 'active';
      if (hasPercent) {
        row._normalStatus = percent + '%';
        row._percent.textContent = row._normalStatus;
      } else {
        row._normalStatus = phase === 'file-start' ? 'Iniciando…' : '—';
        row._percent.textContent = row._normalStatus;
        row._fill.title = 'O servidor não informou o tamanho total do arquivo.';
      }
    }

    if (percent === null) {
      row._fill.style.width = '';
      row._fill.removeAttribute('aria-valuenow');
      if (hasPercent) {
        row._fill.removeAttribute('title');
      } else {
        row._fill.title = 'O servidor não informou o tamanho total do arquivo.';
      }
    } else {
      row._fill.style.width = percent + '%';
      row._fill.setAttribute('aria-valuenow', String(percent));
      row._fill.removeAttribute('title');
    }
    row._percent.textContent = paused && row.dataset.state === 'active'
      ? 'Pausado · ' + row._normalStatus
      : row._normalStatus;
  }

  function update(target, progress) {
    var container = resolveContainer(target);
    if (!container || !progress) return;
    var state = getState(container);
    var key = String(progress.url || ('index:' + (Number(progress.index) || 0)));
    var row = state.rows[key];
    var fileValue = progress.filePercent;
    var hasFilePercent = fileValue !== null && fileValue !== undefined &&
      Number.isFinite(Number(fileValue));
    if (progress.phase === 'file-start') {
      var runTotal = Number(progress.total) || 1;
      if (state.total !== null && state.total !== runTotal) {
        state.percentages = Object.create(null);
      }
      state.total = runTotal;
      state.percentages[key] = 0;
    } else if (progress.phase === 'file-progress' && hasFilePercent) {
      state.percentages[key] = Math.max(0, Math.min(100, Number(fileValue)));
    } else if (progress.phase === 'file-complete') {
      state.percentages[key] = progress.state === 'sucesso'
        ? 100
        : (progress.state === 'indisponivel'
          ? 0
          : (hasFilePercent
            ? Math.max(0, Math.min(100, Number(fileValue)))
            : (state.percentages[key] || 0)));
    } else if (progress.phase === 'queue-progress') {
      if (progress.state === 'sucesso') state.percentages[key] = 100;
      else if (progress.state === 'indisponivel') state.percentages[key] = 0;
    }

    if (progress.phase === 'queue-progress') {
      if (!row) return;
      if (progress.state === 'sucesso') {
        row.dataset.state = 'complete';
        row._normalStatus = 'Concluído · 100%';
        row._percent.textContent = row._normalStatus;
        row._fill.classList.remove('is-indeterminate');
        row._fill.style.width = '100%';
        row._fill.setAttribute('aria-valuenow', '100');
      } else {
        row.dataset.state = 'failed';
        if (progress.state === 'indisponivel') {
          row._normalStatus = 'Indisponível';
          row._percent.textContent = row._normalStatus;
        }
      }
      if (row._removeTimer) window.clearTimeout(row._removeTimer);
      row._removeTimer = window.setTimeout(function () {
        if (state.rows[key] === row && row.dataset.state !== 'active') {
          removeRow(container, state, key);
        }
      }, 1100);
      return;
    }

    if (progress.phase !== 'file-start' &&
        progress.phase !== 'file-progress' &&
        progress.phase !== 'file-complete') return;

    if (row && progress.phase === 'file-start') {
      if (row._removeTimer) window.clearTimeout(row._removeTimer);
      row._removeTimer = null;
    }
    row = row || makeRow(container, state, key);
    if (!row) return;
    setRowProgress(row, progress, progress.paused === true);
  }

  function countActive(target) {
    var container = resolveContainer(target);
    if (!container) return 0;
    return Array.prototype.filter.call(container.children, function (row) {
      return row.dataset.state === 'active';
    }).length;
  }

  function overallPercent(target, total) {
    var state = getState(resolveContainer(target));
    var count = Number(total) || 0;
    if (!state || count <= 0) return 0;
    var sum = Object.keys(state.percentages).reduce(function (value, key) {
      return value + (Number(state.percentages[key]) || 0);
    }, 0);
    return Math.max(0, Math.min(100, Math.round(sum / count)));
  }

  function setPaused(target, paused) {
    var container = resolveContainer(target);
    var state = getState(container);
    if (!state) return;
    state.order.forEach(function (key) {
      var row = state.rows[key];
      if (!row || row.dataset.state !== 'active') return;
      row._percent.textContent = paused
        ? 'Pausado · ' + row._normalStatus
        : row._normalStatus;
    });
  }

  window.DownloadProgressList = {
    reset: reset,
    update: update,
    countActive: countActive,
    overallPercent: overallPercent,
    setPaused: setPaused
  };
})(window, document);
