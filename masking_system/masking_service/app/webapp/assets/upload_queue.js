/* Bound Streamlit's same-origin file PUTs without changing its bundled assets.
 * Streamlit 1.58/1.63 use Axios/XHR. Keep Axios progress, XSRF, errors and
 * cancellation intact. A future transport change needs a browser regression test.
 */
(() => {
  if (window.__maskingUploadQueue) return;
  const limit = 4;
  const prototype = XMLHttpRequest.prototype;
  const nativeOpen = prototype.open;
  const nativeSend = prototype.send;
  const nativeAbort = prototype.abort;
  const eligible = new WeakMap();
  const jobs = new WeakMap();
  const waiting = new Map();
  let active = 0;
  let peak = 0;

  function finish(job) {
    if (!job || job.state === 'done') return;
    if (job.state === 'active') active--;
    job.state = 'done';
    waiting.delete(job.xhr);
    job.xhr.removeEventListener('loadend', job.onEnd);
    if (jobs.get(job.xhr) === job) jobs.delete(job.xhr);
    job.args = null;
    queueMicrotask(pump);
  }

  function pump() {
    while (active < limit && waiting.size) {
      const job = waiting.values().next().value;
      waiting.delete(job.xhr);
      job.state = 'active';
      active++;
      peak = Math.max(peak, active);
      job.xhr.addEventListener('loadend', job.onEnd, { once: true });
      try {
        nativeSend.apply(job.xhr, job.args);
        job.args = null;
      } catch (_) {
        // A queued send has no caller stack left to receive a synchronous
        // exception. Let Axios reject normally instead of leaving its form pending.
        try {
          job.xhr.dispatchEvent(new ProgressEvent('error'));
          job.xhr.dispatchEvent(new ProgressEvent('loadend'));
        } finally {
          finish(job);
        }
      }
    }
  }

  prototype.open = function (method, url, ...rest) {
    finish(jobs.get(this));
    const result = nativeOpen.call(this, method, url, ...rest);
    const target = new URL(url, document.baseURI);
    eligible.set(this,
      String(method).toUpperCase() === 'PUT' && rest[0] !== false &&
      target.origin === location.origin &&
      /\/_stcore\/upload_file\/[^/]+\/[^/]+$/.test(target.pathname));
    return result;
  };

  prototype.send = function (...args) {
    if (!eligible.get(this) || this.readyState !== XMLHttpRequest.OPENED) {
      return nativeSend.apply(this, args);
    }
    if (jobs.has(this)) throw new DOMException('Upload already sent', 'InvalidStateError');
    const job = { xhr: this, args, state: 'queued', onEnd: null };
    job.onEnd = () => finish(job);
    jobs.set(this, job);
    waiting.set(this, job);
    pump();
  };

  prototype.abort = function () {
    const job = jobs.get(this);
    try {
      return nativeAbort.call(this);
    } finally {
      // Queued XHRs have not reached native send, so abort has no loadend event.
      finish(job);
    }
  };

  // Counters only: no file names, contents, session IDs or request URLs.
  window.__maskingUploadQueue = Object.freeze({
    version: 1, limit,
    get active() { return active; },
    get queued() { return waiting.size; },
    get peak() { return peak; },
  });
})();
