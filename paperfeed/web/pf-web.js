/* PaperFeed on a website: the browser's stand-in for `paperfeed serve`.
 *
 * On the Mac, `serve` answers six calls from the page - /api/save, unsave,
 * status, explain, directions, troubleshoot - from library.db and the AI key
 * in the environment. A website has nothing behind it to answer them, so this
 * script answers them instead, in the browser: the library is library.json in
 * a private GitHub repository, and the AI buttons call the AI service
 * directly. The page's own Save and library scripts run unchanged; they never
 * learn the difference. (website.py builds the site and hands this script the
 * card template, those scripts and the AI prompts in pf-web-data.json, so
 * none of them exists twice.)
 *
 * Nothing here is sent anywhere but GitHub and the AI service. The two keys
 * this needs are pasted once into the page and kept in this browser's own
 * storage: never in the site, never in the repository, never in a URL.
 */
(function () {
  'use strict';

  var SLOT = { token: 'paperfeed.githubToken', repo: 'paperfeed.libraryRepo',
               ai: 'paperfeed.aiKey' };
  var LIBRARY_FILE = 'library.json';
  var SETTINGS_FILE = 'settings.json';
  var STATUSES = ['unread', 'reading', 'read'];

  var DATA = null;        // pf-web-data.json
  var LIB = null;         // { sha, data: { records: [...] } }
  var SETTINGS = null;    // settings.json from the private repository

  // localStorage can be missing or throw (private windows, blocked storage);
  // the page must still render, just unconnected.
  function slot(name, value) {
    try {
      if (value === undefined) { return localStorage.getItem(name) || ''; }
      if (value) { localStorage.setItem(name, value); } else { localStorage.removeItem(name); }
    } catch (error) { return ''; }
    return value;
  }
  // On <owner>.github.io the library is most likely <owner>/paperfeed-library.
  // Anywhere else there is nothing to guess from, so the field starts empty.
  function defaultRepo() {
    var host = location.hostname;
    return /\.github\.io$/.test(host) ? host.split('.')[0] + '/paperfeed-library' : '';
  }
  function repo() { return slot(SLOT.repo) || defaultRepo(); }
  // Only ever set to test this script against a stand-in for GitHub; empty
  // in normal use, which means GitHub itself.
  function githubApi() { return slot('paperfeed.githubApi') || 'https://api.github.com'; }
  function connected() { return !!slot(SLOT.token); }

  // ------------------------------------------------------------------------
  // Text helpers
  // ------------------------------------------------------------------------

  // Safe in text AND attribute position (the card template puts values in
  // href="..." and data-key="..."), so quotes are escaped too.
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }
  // Python's str.format for the templates handed over from ai.py and
  // server.py: {name} is a field, {{ and }} are literal braces.
  function fill(template, values) {
    return template
      .replace(/\{\{/g, '\u0000').replace(/\}\}/g, '\u0001')
      .replace(/\{(\w+)\}/g, function (whole, name) {
        return Object.prototype.hasOwnProperty.call(values, name) ? values[name] : whole;
      })
      .replace(/\u0000/g, '{').replace(/\u0001/g, '}');
  }
  function squash(text) { return String(text || '').split(/\s+/).join(' ').trim(); }
  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
  function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, '+00:00'); }

  function toBase64(text) {
    var bytes = new TextEncoder().encode(text), out = '';
    for (var i = 0; i < bytes.length; i += 0x8000) {
      out += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(out);
  }
  function fromBase64(b64) {
    var bin = atob(String(b64).replace(/\s/g, ''));
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i += 1) { bytes[i] = bin.charCodeAt(i); }
    return new TextDecoder().decode(bytes);
  }

  // ------------------------------------------------------------------------
  // GitHub: one private repository, through the contents API
  // ------------------------------------------------------------------------

  function Problem(message, extra) {
    var error = new Error(message);
    for (var name in extra || {}) { error[name] = extra[name]; }
    return error;
  }

  function github(path, options) {
    options = options || {};
    var headers = {
      'Authorization': 'Bearer ' + slot(SLOT.token),
      'X-GitHub-Api-Version': '2022-11-28',
      'Accept': options.raw ? 'application/vnd.github.raw+json' : 'application/vnd.github+json'
    };
    if (options.body) { headers['Content-Type'] = 'application/json'; }
    // no-store: GitHub marks these responses cacheable for 60 seconds, and a
    // stale copy here means a stale library and a write that always conflicts.
    return fetchReal(githubApi() + '/repos/' + repo() + '/contents/' + path, {
      method: options.method || 'GET', headers: headers, cache: 'no-store',
      body: options.body ? JSON.stringify(options.body) : undefined
    }).catch(function () {
      throw Problem('Could not reach GitHub. Are you online?');
    });
  }

  function explainRefusal(response, verb) {
    if (response.status === 401) {
      return Problem('GitHub did not accept the key saved in this browser - it may have ' +
                     'expired. Paste a new one under "Connect this browser".', { auth: true });
    }
    if (response.status === 404) {
      return Problem('GitHub cannot see ' + repo() + '. Check the name, and that the key ' +
                     'was given access to that repository.', { auth: true });
    }
    if (response.status === 403) {
      return Problem('GitHub refused to ' + verb + ' ' + repo() + ' (HTTP 403). The key needs ' +
                     '"Contents: Read and write" on that repository.', { auth: true });
    }
    return Problem('GitHub answered HTTP ' + response.status + ' when asked to ' + verb +
                   ' the library.');
  }

  function getFile(path) {
    return github(path).then(function (response) {
      if (response.status === 404) {
        // Missing file, or missing repository? Only the second is a problem.
        return fetchReal(githubApi() + '/repos/' + repo(), {
          headers: { 'Authorization': 'Bearer ' + slot(SLOT.token) }, cache: 'no-store'
        }).then(function (check) {
          if (!check.ok) { throw explainRefusal(check, 'read'); }
          return { text: null, sha: null };
        });
      }
      if (!response.ok) { throw explainRefusal(response, 'read'); }
      return response.json().then(function (meta) {
        if (meta.content) { return { text: fromBase64(meta.content), sha: meta.sha }; }
        // Over 1 MB GitHub leaves "content" empty and wants a raw request.
        return github(path, { raw: true }).then(function (raw) {
          if (!raw.ok) { throw explainRefusal(raw, 'read'); }
          return raw.text().then(function (text) { return { text: text, sha: meta.sha }; });
        });
      });
    });
  }

  function putFile(path, text, sha, message) {
    var body = { message: message, content: toBase64(text) };
    if (sha) { body.sha = sha; }
    return github(path, { method: 'PUT', body: body }).then(function (response) {
      if ((response.status === 409 || response.status === 422) && sha) {
        throw Problem('changed meanwhile', { conflict: true });
      }
      if (!response.ok) { throw explainRefusal(response, 'write to'); }
      return response.json().then(function (done) { return done.content.sha; });
    });
  }

  // ------------------------------------------------------------------------
  // The library
  // ------------------------------------------------------------------------

  function loadLibrary() {
    return getFile(LIBRARY_FILE).then(function (file) {
      var data = { version: 1, records: [] };
      if (file.text) {
        try { data = JSON.parse(file.text); }
        catch (error) { throw Problem(LIBRARY_FILE + ' in ' + repo() + ' is not valid JSON; it has not been touched.'); }
        if (Array.isArray(data)) { data = { version: 1, records: data }; }
        data.records = data.records || [];
      }
      LIB = { sha: file.sha, data: data };
      return LIB;
    });
  }

  function loadSettings() {
    return getFile(SETTINGS_FILE).then(function (file) {
      SETTINGS = file.text ? JSON.parse(file.text) : null;
      return SETTINGS;
    });
  }

  function byNewest(a, b) {
    return (b.saved_at || '') < (a.saved_at || '') ? -1 : (b.saved_at || '') > (a.saved_at || '') ? 1 : 0;
  }

  function identities(records) {
    var found = {};
    records.forEach(function (record) {
      if (record.key) { found[record.key] = record; }
      if (record.alt_key) { found[record.alt_key] = record; }
    });
    return found;
  }

  function find(key) {
    var records = LIB ? LIB.data.records : [];
    for (var i = 0; i < records.length; i += 1) { if (records[i].key === key) { return records[i]; } }
    return null;
  }

  // Apply one change and write it back. If the file changed since it was read
  // - saved on another device, or the scheduled run added papers - read it
  // again and re-apply the same change to the fresh copy.
  function mutate(message, change) {
    var attempts = 0;
    function attempt(fresh) {
      return (fresh || !LIB ? loadLibrary() : Promise.resolve(LIB)).then(function () {
        var outcome = change(LIB.data.records);
        if (!outcome.changed) { return outcome.result; }
        LIB.data.records.sort(byNewest);
        var text = JSON.stringify(LIB.data, null, 1) + '\n';
        return putFile(LIBRARY_FILE, text, LIB.sha, message).then(function (sha) {
          LIB.sha = sha;
          return outcome.result;
        }, function (error) {
          LIB = null;                         // our copy is no longer the truth
          if (error.conflict && (attempts += 1) < 4) { return attempt(true); }
          throw error;
        });
      });
    }
    return attempt(false);
  }

  // ------------------------------------------------------------------------
  // The AI service. The website supports Gemini, which is what PaperFeed's
  // config names; the Mac's `serve` supports the others too.
  // ------------------------------------------------------------------------

  function interestsFor(setName) {
    var interests = (SETTINGS && SETTINGS.interests) || {};
    return (setName && interests.by_set && interests.by_set[setName]) || interests.general || '';
  }
  function contextLine(interests) {
    interests = String(interests || '').trim();
    return interests ? 'Background on this researcher: ' + interests + '\n\n' : '';
  }
  function listing(papers) {
    var limit = DATA.prompts.library_limit;
    return papers.slice(0, limit).map(function (record) {
      return '- ' + [record.title || '', record.venue || record.source || '',
                     'doi:' + (record.doi || 'none'), squash(record.abstract).slice(0, 400)].join(' | ');
    }).join('\n');
  }
  function validatedDois(values, known) {
    var out = [];
    (values || []).forEach(function (value) {
      var doi = String(value).trim().toLowerCase().replace(/^doi:/, '');
      if (doi && known[doi] && out.indexOf(doi) < 0) { out.push(doi); }
    });
    return out;
  }
  function knownDois(papers) {
    var known = {};
    papers.forEach(function (paper) { if (paper.doi) { known[String(paper.doi).toLowerCase()] = true; } });
    return known;
  }

  function aiReady() {
    if (!slot(SLOT.ai)) {
      return 'no AI key in this browser - add it under "Connect this browser" at the bottom of the library page';
    }
    if (!SETTINGS) {
      return 'the library repository has no settings.json yet - it is written by the next scheduled run';
    }
    if (!SETTINGS.ai_enabled) { return 'AI is switched off in config.json (ai.enabled)'; }
    if (SETTINGS.provider !== 'gemini') {
      return 'the website\'s AI buttons use Google Gemini, but ai.provider is "' + SETTINGS.provider +
             '" - `paperfeed serve` on the Mac supports the others';
    }
    return '';
  }

  function sleep(ms) { return new Promise(function (done) { setTimeout(done, ms); }); }

  function callAI(model, system, userText, maxTokens, effort) {
    var base = (SETTINGS.base_url || 'https://generativelanguage.googleapis.com/v1beta').replace(/\/+$/, '');
    var body = {
      systemInstruction: { parts: [{ text: system }] },
      contents: [{ role: 'user', parts: [{ text: userText }] }],
      // Thinking tokens come out of maxOutputTokens; off for short asks, as ai.py does.
      generationConfig: { maxOutputTokens: maxTokens, thinkingConfig: { thinkingBudget: effort ? 2048 : 0 } }
    };
    var tries = 0;
    function once() {
      tries += 1;
      return fetchReal(base + '/models/' + encodeURIComponent(model) + ':generateContent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-goog-api-key': slot(SLOT.ai) },
        body: JSON.stringify(body)
      }).catch(function () {
        throw Problem('could not reach the AI service. Are you online?');
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (reply) {
          var said = (reply.error && reply.error.message) || '';
          if (response.status === 429) {
            // Said plainly: a daily allowance cannot be waited out in a minute.
            throw Problem('Google refused: the allowance for ' + model + ' is used up for now (HTTP 429). ' + said);
          }
          if ([500, 502, 503, 529].indexOf(response.status) >= 0 && tries < 3) {
            return sleep(2000 * tries).then(once);
          }
          if (!response.ok) {
            if (/api key not valid|api_key_invalid/i.test(said)) {
              throw Problem('Google did not accept the AI key saved in this browser.');
            }
            throw Problem('the AI service answered HTTP ' + response.status + (said ? ': ' + said : ''));
          }
          var candidate = (reply.candidates || [])[0] || {};
          var text = ((candidate.content || {}).parts || []).map(function (part) { return part.text || ''; }).join('');
          if (!text.trim()) {
            throw Problem(candidate.finishReason === 'MAX_TOKENS'
              ? 'the answer ran out of room before it started' : 'the AI service returned nothing');
          }
          return text;
        });
      });
    }
    return once();
  }

  function extractArray(text) {
    var start = text.indexOf('['), end = text.lastIndexOf(']');
    if (start < 0 || end < start) { throw Problem('no JSON array in the reply'); }
    var parsed;
    try { parsed = JSON.parse(text.slice(start, end + 1)); }
    catch (error) { throw Problem('the reply was not valid JSON (' + error.message + ')'); }
    if (!Array.isArray(parsed)) { throw Problem('expected a JSON array'); }
    return parsed;
  }

  function newestPapers() {
    return LIB.data.records.slice().sort(byNewest).slice(0, 200);
  }

  // ------------------------------------------------------------------------
  // The six calls `serve` answers on the Mac, answered here instead.
  // Same requests, same replies - see server.py Handler.do_POST.
  // ------------------------------------------------------------------------

  var API = {
    save: function (body) {
      var keys = body.keys || [];
      if (!body.title) { return { ok: false, error: 'no title' }; }
      if (!keys.length) { return { ok: false, error: 'this page carries no paper identities; open a newer digest' }; }
      return mutate('Save: ' + String(body.title).slice(0, 80), function (records) {
        var known = identities(records);
        for (var i = 0; i < keys.length; i += 1) {
          if (known[keys[i]]) { return { changed: false, result: { ok: true, key: known[keys[i]].key, already: true } }; }
        }
        records.push({
          key: keys[0], alt_key: keys[1] || '', doi: body.doi || '', title: body.title,
          authors: body.authors || [], abstract: body.abstract || '', venue: body.venue || '',
          published: body.published || '', source: body.source || '', url: body.url || '',
          set_name: body.set_name || '', score: Number(body.score) || 0, note: '', tags: '',
          status: 'unread', origin: 'you', ai_summary: '', saved_at: nowIso()
        });
        return { changed: true, result: { ok: true, key: keys[0], already: false } };
      });
    },
    unsave: function (body) {
      return mutate('Remove from library', function (records) {
        var before = records.length;
        for (var i = records.length - 1; i >= 0; i -= 1) {
          if (records[i].key === body.key) { records.splice(i, 1); }
        }
        return { changed: records.length !== before, result: { ok: true, removed: records.length !== before } };
      });
    },
    status: function (body) {
      if (STATUSES.indexOf(body.status) < 0) { return { ok: false }; }
      return mutate('Mark ' + body.status, function (records) {
        for (var i = 0; i < records.length; i += 1) {
          if (records[i].key === body.key) {
            var changed = records[i].status !== body.status;
            records[i].status = body.status;
            return { changed: changed, result: { ok: true } };
          }
        }
        return { changed: false, result: { ok: false } };
      });
    },
    explain: function (body) {
      var record = find(body.key);
      if (!record) { return { ok: false, error: 'that paper is not in your library' }; }
      var problem = aiReady();
      if (problem) { return { ok: false, error: problem }; }
      var prompt = fill(DATA.prompts.explain_template, {
        context: contextLine(interestsFor(record.set_name)),
        title: record.title || '',
        venue: record.venue || record.source || '',
        abstract: squash(record.abstract).slice(0, 4000) || '(no abstract available - say so rather than guessing)'
      });
      return callAI(SETTINGS.models.ai, DATA.prompts.explain_system, prompt, 700, null).then(function (text) {
        var summary = text.trim();
        return mutate('Explain: ' + String(record.title).slice(0, 80), function (records) {
          for (var i = 0; i < records.length; i += 1) {
            if (records[i].key === record.key) {
              records[i].ai_summary = summary;
              return { changed: true, result: { ok: true, summary: summary, cost: null } };
            }
          }
          // Removed on another device while the model was thinking: still show it.
          return { changed: false, result: { ok: true, summary: summary, cost: null } };
        });
      });
    },
    directions: function () {
      var problem = aiReady();
      if (problem) { return { ok: false, error: problem }; }
      var papers = newestPapers();
      if (!papers.length) { return { ok: false, error: 'there is nothing saved yet' }; }
      var prompt = fill(DATA.prompts.directions_template, {
        context: contextLine(interestsFor(null)), papers: listing(papers)
      });
      return callAI(SETTINGS.models.trends, DATA.prompts.directions_system, prompt, 4000, 'medium')
        .then(function (text) {
          var known = knownDois(papers);
          var directions = extractArray(text).filter(function (row) {
            return row && typeof row === 'object' && row.direction;
          }).map(function (row) {
            return {
              direction: String(row.direction).slice(0, 160),
              why: String(row.why || '').slice(0, 2000),
              first_step: String(row.first_step || '').slice(0, 800),
              papers: validatedDois(row.papers, known)
            };
          });
          return { ok: true, directions: directions, cost: null };
        });
    },
    troubleshoot: function (body) {
      var problem = aiReady();
      if (problem) { return { ok: false, error: problem }; }
      var question = String(body.question || '').trim();
      if (!question) { return { ok: false, error: 'no question was asked' }; }
      var papers = newestPapers();
      if (!papers.length) { return { ok: false, error: 'there is nothing saved yet to answer from' }; }
      var used = Math.min(papers.length, DATA.prompts.library_limit);
      var prompt = fill(DATA.prompts.troubleshoot_template, {
        context: contextLine(interestsFor(null)), question: question.slice(0, 2000), papers: listing(papers)
      });
      return callAI(SETTINGS.models.trends, DATA.prompts.troubleshoot_system, prompt, 4000, 'medium')
        .then(function (text) {
          var total = LIB.data.records.length;
          var start = text.indexOf('{'), end = text.lastIndexOf('}'), parsed = null;
          if (start >= 0 && end > start) {
            try { parsed = JSON.parse(text.slice(start, end + 1)); } catch (error) { parsed = null; }
          }
          if (!parsed) {       // better prose we did not expect than no answer
            return { ok: true, result: { answer: text.trim(), suggestions: [], papers: [], gap: '', used: used, total: total } };
          }
          return { ok: true, cost: null, result: {
            used: used, total: total,
            answer: String(parsed.answer || '').slice(0, 4000),
            suggestions: (parsed.suggestions || []).slice(0, 6).map(function (s) { return String(s).slice(0, 400); }),
            papers: validatedDois(parsed.papers, knownDois(papers)),
            gap: String(parsed.gap || '').slice(0, 800)
          } };
        });
    }
  };

  // Every fetch to /api/<one of the six> is answered here; anything else goes
  // out as normal. The page's scripts call fetch('/api/save', ...) exactly as
  // they do against `serve`.
  var fetchReal = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var match = /\/api\/(save|unsave|status|explain|directions|troubleshoot)$/.exec(
      new URL(url, location.href).pathname);
    if (!match) { return fetchReal(input, init); }
    var body = {};
    try { body = JSON.parse((init && init.body) || '{}'); } catch (error) { body = {}; }
    return Promise.resolve()
      .then(function () { return API[match[1]](body); })
      .catch(function (error) { return { ok: false, error: String((error && error.message) || error) }; })
      .then(function (payload) {
        return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
      });
  };

  // ------------------------------------------------------------------------
  // Pages
  // ------------------------------------------------------------------------

  function loadData() {
    if (DATA) { return Promise.resolve(DATA); }
    return fetchReal('pf-web-data.json', { cache: 'no-cache' })
      .then(function (response) { return response.json(); })
      .then(function (data) { DATA = data; return data; });
  }

  function runScript(text) {
    var node = document.createElement('script');
    node.textContent = text;
    document.body.appendChild(node);
  }
  function addStyle(text) {
    var node = document.createElement('style');
    node.textContent = text;
    document.head.appendChild(node);
  }

  // A digest page: what build_page() injects on the Mac, done here.
  function digestPage() {
    var blobs = document.querySelectorAll('.paper script.pf-paper');
    if (!blobs.length || !connected()) { return; }
    var wrap = document.querySelector('.wrap');
    var bar = document.createElement('div');
    bar.className = 'pf-bar';
    bar.textContent = 'PaperFeed - reading your library…';
    if (wrap) { wrap.insertBefore(bar, wrap.firstChild); }
    Promise.all([loadData(), loadLibrary()]).then(function () {
      var known = identities(LIB.data.records);
      var state = Array.prototype.map.call(document.querySelectorAll('.paper'), function (card) {
        var blob = card.querySelector('script.pf-paper'), payload = {};
        try { payload = JSON.parse(blob ? blob.textContent : '{}'); } catch (error) { payload = {}; }
        var keys = payload.keys || [];
        var saved = keys.some(function (key) { return known[key]; });
        return { key: keys[0] || '', saved: saved };
      });
      var savedNow = {};
      state.forEach(function (entry) { if (entry.saved) { savedNow[entry.key] = true; } });
      bar.innerHTML = 'PaperFeed &mdash; click <b>+ Save</b> to keep a paper. ' +
        '<span id="pf-count">' + Object.keys(savedNow).length + '</span> on this page are in your library. ' +
        '<a href="library.html">View library</a> &middot; <a href="dashboard.html">Dashboard</a>';
      addStyle(DATA.save_css);
      var holder = document.createElement('script');
      holder.id = 'pf-state';
      holder.type = 'application/json';
      holder.textContent = JSON.stringify(state).replace(/</g, '\\u003c');
      document.body.appendChild(holder);
      window.PF_LIBRARY_URL = 'library.html';
      runScript(DATA.save_js);
    }).catch(function (error) {
      bar.className = 'pf-bar pf-stale';
      bar.innerHTML = esc(error.message) + ' <a href="library.html#connect">Fix the connection</a>';
    });
  }

  function card(record, hasKey) {
    var c = DATA.card, status = record.status || 'unread', summary = record.ai_summary || '';
    var tail = [record.source, record.venue, record.published].filter(Boolean).join(' · ');
    return fill(c.template, {
      key: esc(record.key), status: esc(status), url: esc(record.url || ''),
      title: esc(record.title || ''), authors: esc((record.authors || []).slice(0, 6).join(', ')),
      tail: esc(tail), extras: record.extras_html || '',
      buttons: c.statuses.map(function (pair) {
        return fill(c.status_button, { on: status === pair[0] ? ' on' : '', value: pair[0], label: pair[1] });
      }).join(''),
      explain: hasKey ? fill(c.explain_button, { label: summary ? 'Explain again' : 'Explain this' }) : '',
      out_style: summary ? '' : c.out_hidden,
      summary: esc(summary)
    });
  }

  var CONNECT_HELP =
    '<p class="hint">Save, your library and the AI buttons need two keys, pasted here once. ' +
    'They stay in <b>this browser only</b> and are sent only to GitHub and to Google &mdash; ' +
    'never into the site or the repository. Do this once on each phone or computer.</p>' +
    '<p class="hint"><b>1. GitHub key.</b> Create one at ' +
    '<a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">' +
    'GitHub &rarr; Fine-grained tokens</a>: <i>Repository access</i> &rarr; <i>Only select repositories</i> ' +
    '&rarr; <b>{repo_name}</b>; <i>Permissions</i> &rarr; <i>Contents</i> &rarr; <b>Read and write</b>. ' +
    'Nothing else. Copy the key it shows you and paste it below.</p>' +
    '<p class="hint"><b>2. AI key</b> (optional, for Explain and the two tools above): your Google AI Studio ' +
    'key, from <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>.</p>';

  function connectPanel(message) {
    var box = document.getElementById('pf-connect');
    if (!box) { return; }
    var repoName = repo();
    box.innerHTML =
      '<div class="tools" id="connect"><h3>Connect this browser</h3>' +
      (message ? '<p class="hint" style="color:#8a2a20"><b>' + esc(message) + '</b></p>' : '') +
      fill(CONNECT_HELP, { repo_name: esc(repoName || 'paperfeed-library') }) +
      '<p><label>GitHub key<br><input id="pf-in-token" type="password" autocomplete="off" ' +
      'style="width:100%;max-width:520px" placeholder="github_pat_..."></label></p>' +
      '<p><label>AI key (optional)<br><input id="pf-in-ai" type="password" autocomplete="off" ' +
      'style="width:100%;max-width:520px" placeholder="' + (slot(SLOT.ai) ? 'saved - leave empty to keep' : 'AIza...') + '"></label></p>' +
      '<p><label>Library repository<br><input id="pf-in-repo" type="text" autocomplete="off" ' +
      'style="width:100%;max-width:520px" value="' + esc(repoName) + '"></label></p>' +
      '<button class="act primary" id="pf-connect-go">Connect</button> ' +
      (connected() ? '<button class="act" id="pf-disconnect">Disconnect this browser</button>' : '') +
      '</div>';
    document.getElementById('pf-connect-go').addEventListener('click', function () {
      var token = document.getElementById('pf-in-token').value.trim();
      var aiKey = document.getElementById('pf-in-ai').value.trim();
      var name = document.getElementById('pf-in-repo').value.trim();
      if (token) { slot(SLOT.token, token); }
      if (aiKey) { slot(SLOT.ai, aiKey); }
      if (name) { slot(SLOT.repo, name === defaultRepo() ? '' : name); }
      if (!connected()) { connectPanel('Paste the GitHub key first.'); return; }
      location.hash = '';
      location.reload();
    });
    var off = document.getElementById('pf-disconnect');
    if (off) {
      off.addEventListener('click', function () {
        slot(SLOT.token, ''); slot(SLOT.ai, ''); slot(SLOT.repo, '');
        location.reload();
      });
    }
  }

  function connectionFooter() {
    var box = document.getElementById('pf-connect');
    if (!box) { return; }
    box.innerHTML = '<p class="meta" style="margin:0 0 12px">This browser is connected to ' +
      '<b>' + esc(repo()) + '</b>' + (slot(SLOT.ai) ? ' and has an AI key' : ', without an AI key') +
      ' &middot; <a href="#connect" id="pf-change">change</a></p>';
    document.getElementById('pf-change').addEventListener('click', function (event) {
      event.preventDefault();
      connectPanel('');
    });
  }

  function libraryPage() {
    var mount = document.getElementById('pf-cards');
    var countLine = document.getElementById('pf-count-line');
    if (!connected()) {
      countLine.textContent = 'This browser is not connected to your library yet.';
      connectPanel('');
      return;
    }
    if (location.hash === '#connect') { connectPanel(''); }
    Promise.all([loadData(), loadLibrary(), loadSettings()]).then(function () {
      var records = LIB.data.records.slice().sort(byNewest);
      var hasKey = !aiReady();
      countLine.textContent = plural(records.length, 'saved paper');
      mount.innerHTML = records.length
        ? records.map(function (record) { return card(record, hasKey); }).join('\n')
        : '<div class="empty">Nothing saved yet. Open the <a href="latest.html">digest</a> and click ' +
          '<b>+ Save</b> on anything worth keeping.</div>';
      document.querySelector('.pills').hidden = false;
      document.getElementById('pf-ai').hidden = !hasKey;
      if (location.hash !== '#connect') { connectionFooter(); }
      runScript(DATA.library_js);        // binds to the cards now on the page
      if (window.refreshCounts) { window.refreshCounts(); }
      if (window.pfApplyFilters) { window.pfApplyFilters(); }
    }).catch(function (error) {
      countLine.textContent = 'Could not open your library.';
      connectPanel(error.message);
    });
  }

  function start() {
    if (document.getElementById('pf-cards')) { libraryPage(); } else { digestPage(); }
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', start); } else { start(); }
})();
