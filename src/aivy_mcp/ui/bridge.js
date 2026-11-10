/* Aivy card bridge: one small shim so the same HTML renders inside
   ChatGPT (OpenAI Apps SDK, window.openai) and inside any MCP Apps host
   (JSON-RPC over postMessage, spec 2026-01-26). It hands the tool's
   structured result to the card, relays the theme, lets the card call a
   tool or open a link, and reports its height. No network, no assets. */
(function () {
  var B = window.AivyBridge = { data: null, host: null, theme: null, _listeners: [] };
  B.onData = function (fn) { B._listeners.push(fn); if (B.data) fn(B.data); };
  var lastData = '';
  function setData(d) {
    if (!d) return;
    var key = ''; try { key = JSON.stringify(d); } catch (e) {}
    if (key && key === lastData) return;   /* the same result may arrive on both channels */
    lastData = key; B.data = d; B._listeners.forEach(function (f) { try { f(d); } catch (e) { console.error(e); } });
  }
  function setTheme(t) { B.theme = t === 'dark' ? 'dark' : 'light'; document.documentElement.setAttribute('data-theme', B.theme); }
  function height() { return Math.ceil(document.documentElement.getBoundingClientRect().height); }

  /* Hosts. ChatGPT and Codex mount the card in an MCP Apps sandbox and also
     expose window.openai on top; Claude Desktop is a plain MCP Apps host.
     Every MCP Apps host waits for the ui/initialize handshake before it shows
     the frame (Codex gives up after 5 s with "MCP app initialization timed
     out"), so the handshake runs whenever the card is framed, and
     window.openai only adds the ChatGPT extras: an early copy of the tool
     output, the theme, and its own call/link/height APIs. */
  var oa = window.openai || null;
  var framed = !!(window.parent && window.parent !== window);
  if (oa) {
    setTheme(oa.theme);
    window.addEventListener('openai:set_globals', function (e) {
      var g = (e.detail && e.detail.globals) || {};
      if (g.theme) setTheme(g.theme);
      if (g.toolOutput) setData(g.toolOutput);
    });
    if (oa.toolOutput) setData(oa.toolOutput);
  }
  if (framed) {
    B.host = oa ? 'mcp-apps+openai' : 'mcp-apps';
    var nextId = 0, pending = {};
    function send(msg) { window.parent.postMessage(msg, '*'); }
    function request(method, params) {
      return new Promise(function (resolve, reject) {
        var id = ++nextId; pending[id] = { resolve: resolve, reject: reject };
        send({ jsonrpc: '2.0', id: id, method: method, params: params || {} });
      });
    }
    window.addEventListener('message', function (ev) {
      var m = ev.data; if (!m || m.jsonrpc !== '2.0') return;
      if (m.id !== undefined && !m.method) {
        var p = pending[m.id]; if (!p) return; delete pending[m.id];
        if (m.error) p.reject(m.error); else p.resolve(m.result); return;
      }
      if (m.method === 'ui/notifications/tool-result') {
        var r = m.params || {};
        if (r.structuredContent) setData(r.structuredContent);
        else if (r.content && r.content[0] && r.content[0].text) { try { setData(JSON.parse(r.content[0].text)); } catch (e) {} }
      } else if (m.method === 'ui/notifications/host-context-changed') {
        if (m.params && m.params.theme) setTheme(m.params.theme);
      } else if (m.method === 'ping' && m.id !== undefined) {
        send({ jsonrpc: '2.0', id: m.id, result: {} });
      }
    });
    request('ui/initialize', { protocolVersion: '2026-01-26', appInfo: { name: 'aivy', version: '0.11.2' }, appCapabilities: {} })
      .then(function (res) {
        var hc = (res && res.hostContext) || {};
        if (hc.theme) setTheme(hc.theme);
        send({ jsonrpc: '2.0', method: 'ui/notifications/initialized' });
      }).catch(function () {});
    /* Some hosts mount the card without a tool-call bridge (Claude Desktop's
       Code tab, anthropics/claude-ai-mcp#1181): the call fails with "IPC not
       available for MCP tool call" before it reaches the server. Say what
       the person can do instead of showing the raw message. */
    function friendly(err) {
      var msg = (err && (err.message || (err.data && err.data.message))) || '';
      if (/IPC not available/i.test(msg)) {
        var e = new Error('This chat app cannot run card buttons here. Ask the assistant to do it in the chat instead, or use the web page.');
        e.code = 'host-no-ipc'; return e;
      }
      return err instanceof Error ? err : new Error(msg || 'The call failed');
    }
    B.callTool = function (name, args) {
      var p = oa && oa.callTool ? oa.callTool(name, args) : request('tools/call', { name: name, arguments: args });
      return p.catch(function (e) { throw friendly(e); });
    };
    B.openLink = function (url) { return oa && oa.openExternal ? oa.openExternal({ href: url }) : request('ui/open-link', { url: url }); };
    B.resize = function () {
      send({ jsonrpc: '2.0', method: 'ui/notifications/size-changed', params: { width: document.documentElement.scrollWidth, height: height() } });
      if (oa && oa.notifyIntrinsicHeight) try { oa.notifyIntrinsicHeight(height()); } catch (e) {}
    };
  } else if (oa) {
    B.host = 'openai';
    B.callTool = function (name, args) { return oa.callTool(name, args); };
    B.openLink = function (url) { return oa.openExternal({ href: url }); };
    B.resize = function () { try { oa.notifyIntrinsicHeight(height()); } catch (e) {} };
  } else {
    B.host = 'standalone';
    B.callTool = function () { return Promise.reject(new Error('no host')); };
    B.openLink = function (url) { window.open(url, '_blank'); };
    B.resize = function () {};
    if (window.AIVY_SAMPLE) setTimeout(function () { setData(window.AIVY_SAMPLE); }, 0);
  }
  if (!B.theme) setTheme(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  if (window.ResizeObserver) new ResizeObserver(function () { B.resize(); }).observe(document.documentElement);

  /* Brand: the Aivy mark (inlined by render_template) leads every card footer. */
  B.logo = '/*LOGO*/';
  function brand() {
    if (B.logo.indexOf('data:') !== 0) return;
    var foots = document.querySelectorAll('.card-foot');
    for (var i = 0; i < foots.length; i++) {
      if (foots[i].querySelector('.aivy-logo')) continue;
      var img = document.createElement('img'); img.className = 'aivy-logo'; img.src = B.logo; img.alt = 'Aivy'; img.width = 16; img.height = 16;
      foots[i].insertBefore(img, foots[i].firstChild);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', brand); else brand();

  /* helpers shared by the cards */
  B.kw = function (w, digits) { if (w === null || w === undefined || isNaN(w)) return '--'; var kw = Math.abs(w) / 1000; return kw.toFixed(digits === undefined ? (kw >= 10 ? 1 : 2) : digits) + ' kW'; };
  B.money = function (d) { return d === null || d === undefined ? '--' : '$' + Number(d).toFixed(2); };
  B.esc = function (s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  B.siteLabel = function (d) { if (d.name) return d.name; var id = d.id || d.site_id || ''; var i = id.indexOf(':'); return i > 0 ? id.slice(i + 1) : id; };
  B.vendor = function (d) { var id = d.id || d.site_id || ''; var i = id.indexOf(':'); return i > 0 ? id.slice(0, i) : ''; };
})();
