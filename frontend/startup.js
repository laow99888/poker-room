// Keep this bootstrap readable by older browsers so module failures remain visible.
(function () {
  'use strict';
  var panel = document.getElementById('startup-status');
  var message = document.getElementById('startup-message');
  var detail = document.getElementById('startup-detail');
  var ready = false;
  var timer;
  var supported = 'noModule' in document.createElement('script') && typeof BigInt === 'function'
    && typeof structuredClone === 'function' && typeof AbortController === 'function';

  function fail(reason) {
    if (ready) return;
    clearTimeout(timer);
    panel.hidden = false;
    panel.setAttribute('role', 'alert');
    message.textContent = '牌桌加载失败，请重新加载。若仍失败，请反馈下方错误信息。';
    detail.textContent = String(reason || '脚本未能完成初始化');
    document.getElementById('tg-create').disabled = true;
  }

  window.PaishiStartup = {
    supported: supported,
    ready: function () {
      ready = true;
      clearTimeout(timer);
      panel.hidden = true;
      document.getElementById('tg-create').disabled = false;
    },
    fail: fail
  };

  document.getElementById('startup-reload').onclick = function () {
    location.replace(location.pathname + '?reload=' + Date.now());
  };
  window.addEventListener('error', function (event) {
    if (event.target && event.target.tagName === 'SCRIPT' && event.target.id === 'app-module') {
      fail('应用模块加载失败，可能是网络、缓存或文件版本不一致');
    } else if (event.message) {
      fail(event.message);
    }
  }, true);
  window.addEventListener('unhandledrejection', function (event) {
    fail(event.reason && event.reason.message ? event.reason.message : event.reason);
  });
  timer = setTimeout(function () { fail('等待脚本启动超时，请检查网络或浏览器是否拦截脚本'); }, 15000);
  if (!supported) {
    fail('浏览器内核缺少所需功能，请更新浏览器及 Android System WebView，或使用新版 Chrome / Safari');
  }
}());
