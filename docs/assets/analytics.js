/* Public readership only. Account and submission pages never load this file. */
(function () {
  'use strict';
  const source = document.currentScript;
  if (!source) return;
  const scriptId = source.dataset.scriptId;
  const pageUrl = source.dataset.pageUrl;
  if (!/^pa-[A-Za-z0-9_-]{1,128}$/.test(scriptId || '') || !pageUrl) return;

  // Set options before loading Plausible, including on a very fast connection.
  window.plausible = window.plausible || function () {
    (window.plausible.q = window.plausible.q || []).push(arguments);
  };
  window.plausible.init = window.plausible.init || function (options) {
    window.plausible.o = options || {};
  };
  window.plausible.init({
    outboundLinks: false,
    fileDownloads: false,
    formSubmissions: false,
    hashBasedRouting: false,
    transformRequest: function (payload) {
      // Use the server's public URL; never send search text or campaign tags.
      payload.u = pageUrl;
      if (payload.r) {
        try {
          const referrer = new URL(payload.r);
          payload.r = /^https?:$/.test(referrer.protocol) ? referrer.origin : null;
        } catch (_) {
          payload.r = null;
        }
      }
      delete payload.p;
      return payload;
    }
  });
  const tracker = document.createElement('script');
  tracker.async = true;
  tracker.src = 'https://plausible.io/js/' + scriptId + '.js';
  document.head.appendChild(tracker);
}());
