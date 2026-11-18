const fs = require('fs');
const os = require('os');
const path = require('path');

function loadDotEnv() {
  const envPath = path.join(__dirname, '.env');
  if (!fs.existsSync(envPath)) return;
  try {
    const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
    for (const raw of lines) {
      let line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      if (line.startsWith('export ')) line = line.slice(7).trim();
      const eq = line.indexOf('=');
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      let val = line.slice(eq + 1).trim();
      if (val.length >= 2 && val[0] === val[val.length - 1] && (val[0] === '"' || val[0] === "'")) {
        val = val.slice(1, -1);
      }
      if (key && !(key in process.env)) {
        process.env[key] = val;
      }
    }
  } catch (e) {}
}

loadDotEnv();

const CDP_URL = (process.env.SCHOOL_CDP_URL || 'http://127.0.0.1:9222').replace(/\/+$/, '');
const CLASSROOM_URL = (process.env.SCHOOL_CLASSROOM_URL || 'https://classroom.google.com').replace(/\/+$/, '');
const CLASSROOM_HOST = (() => {
  try { return new URL(CLASSROOM_URL).host; } catch (e) { return 'classroom.google.com'; }
})();
const CLASSROOM_ACCOUNT_INDEX = (process.env.SCHOOL_CLASSROOM_ACCOUNT_INDEX || '0').trim() || '0';
const OUTLOOK_HOSTS = (process.env.SCHOOL_OUTLOOK_HOSTS || 'outlook.cloud.microsoft,outlook.live.com')
  .split(',')
  .map(s => s.trim().toLowerCase())
  .filter(Boolean);
const ACTIVE_PORT_FILE = (
  process.env.SCHOOL_CHROMIUM_PORT_FILE ||
  path.join(os.homedir(), '.config', 'chromium', 'DevToolsActivePort')
).replace(/^~(?=\/|$)/, os.homedir());

async function getPages() {
  // First try direct HTTP json/list
  try {
    const res = await fetch(`${CDP_URL}/json/list`, { signal: AbortSignal.timeout(2000) });
    if (res.ok) {
      const list = await res.json();
      return list.filter(p => p.type === 'page' && p.webSocketDebuggerUrl);
    }
  } catch (e) {}

  // Fallback to active port file
  if (fs.existsSync(ACTIVE_PORT_FILE)) {
    try {
      const [port] = fs.readFileSync(ACTIVE_PORT_FILE, 'utf8').trim().split('\n');
      const res = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(2000) });
      if (res.ok) {
        const list = await res.json();
        return list.filter(p => p.type === 'page' && p.webSocketDebuggerUrl);
      }
    } catch (e) {}
  }

  return [];
}

async function evaluatePage(wsUrl, script, timeoutMs = 3500) {
  return new Promise((resolve) => {
    let ws;
    let timer = setTimeout(() => {
      try { ws && ws.close(); } catch (e) {}
      resolve(null);
    }, timeoutMs);

    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      clearTimeout(timer);
      resolve(null);
      return;
    }

    ws.onopen = () => {
      ws.send(JSON.stringify({
        id: 1,
        method: 'Runtime.evaluate',
        params: { expression: script, returnByValue: true }
      }));
    };

    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        if (data.id === 1) {
          clearTimeout(timer);
          ws.close();
          const val = (data.result && data.result.result) ? data.result.result.value : (data.result ? data.result.value : null);
          resolve(val);
        }
      } catch (e) {}
    };

    ws.onerror = () => {
      clearTimeout(timer);
      try { ws.close(); } catch (e) {}
      resolve(null);
    };
  });
}

async function harvest() {
  const pages = await getPages();
  const results = {
    classroom: [],
    outlook: []
  };

  if (!pages || pages.length === 0) {
    console.log(JSON.stringify(results));
    return;
  }

  for (const p of pages) {
    const url = p.url || '';

    // 1. Google Classroom
    if (url.includes(CLASSROOM_HOST)) {
      const crScript = `(() => {
        const title = document.title;
        const pageUrl = window.location.href;
        const main = document.querySelector('main') || document.body;

        let courseId = '';
        if (pageUrl.includes('/c/')) {
          courseId = pageUrl.split('/c/')[1].split('/')[0];
        } else if (pageUrl.includes('/w/')) {
          courseId = pageUrl.split('/w/')[1].split('/')[0];
        }

        // Check if viewing a specific assignment submission page
        let currentSubmission = null;
        const btnTexts = Array.from(document.querySelectorAll('button, div[role="button"]')).map(b => (b.innerText || '').trim().toLowerCase());
        const pageText = (main.innerText || '').toLowerCase();
        if (btnTexts.some(b => b.includes('annuler la remise') || b.includes('unsubmit')) ||
            pageText.includes('handed in') || pageText.includes('remis en retard') || pageText.includes('votre devoir') || pageText.includes('your work')) {
          if (btnTexts.some(b => b.includes('unsubmit') || b.includes('annuler la remise')) || pageText.includes('handed in') || pageText.includes('remis')) {
            currentSubmission = 'SUBMITTED';
          }
        }
        if (!currentSubmission) {
          if (btnTexts.some(b => b.includes('rendre le devoir') || b.includes('turn in') || b.includes('marquer comme terminé') || b.includes('mark as done'))) {
            currentSubmission = 'NOT_SUBMITTED';
          }
        }

        const assignments = [];
        const seenUrls = new Set();

        const extractDueText = (txt) => {
          if (!txt) return '';
          const lines = txt.split('\\n').map(s => s.trim()).filter(Boolean);
          for (const line of lines) {
            if (/^(aucune date limite|no due date)$/i.test(line)) return '';
            const m = line.match(/^(?:date limite|échéance|due(?:\\s+date)?)\\s*[:\\-]?\\s*(.+)$/i);
            if (m && m[1]) {
              const val = m[1].trim();
              if (!/^(aucune date limite|no due date)$/i.test(val)) return val;
            }
          }
          return '';
        };

        // 1. If current page is already an assignment details page, register it FIRST with clean title from document.title
        if (pageUrl.includes('/c/') && (pageUrl.includes('/a/') || pageUrl.includes('/sa/') || pageUrl.includes('/m/'))) {
          let cleanPageHeading = title.split(' - Classroom')[0].split(' – Classroom')[0].trim();
          cleanPageHeading = cleanPageHeading.replace(/^Assignment:\\s*"?/i, '').replace(/^Material:\\s*'?/i, '').replace(/["']$/g, '').trim();

          if (cleanPageHeading && cleanPageHeading.length > 2) {
            seenUrls.add(pageUrl);
            const mainTxt = main.innerText || '';
            assignments.push({
              title: cleanPageHeading,
              submission_status: currentSubmission || 'UNKNOWN',
              due_text: extractDueText(mainTxt),
              url: pageUrl,
              raw: mainTxt.substring(0, 300)
            });
          }
        }

        // 2. Scan stream items with [data-stream-item-id] (Stream View)
        const streamElements = Array.from(document.querySelectorAll('[data-stream-item-id]'));
        for (const el of streamElements) {
          const streamId = el.getAttribute('data-stream-item-id');
          if (!streamId) continue;
          const b64 = btoa(streamId);
          const txt = el.innerText || '';
          const lower = txt.toLowerCase();

          let subStatus = 'UNKNOWN';
          if (lower.includes('annuler la remise') || lower.includes('remis') || lower.includes('turned in') || lower.includes('handed in') || lower.includes('noté') || lower.includes('graded')) {
            subStatus = 'SUBMITTED';
          } else if (lower.includes('attribué') || lower.includes('assigned') || lower.includes('manquant') || lower.includes('missing') || lower.includes('rendre')) {
            subStatus = 'NOT_SUBMITTED';
          }

          const h2 = el.querySelector('h2');
          const titleSpan = el.querySelector('.Vu2fZd, .QUEiXc');
          let aTitle = h2 ? h2.innerText.trim() : (titleSpan ? titleSpan.innerText.trim() : txt.split('\\n')[0].trim());
          aTitle = aTitle.replace(/^assignment:?\\s*"?/i, '').replace(/^material:?\\s*'?/i, '').replace(/["']?\\s*$/i, '').trim();

          const lowerTitle = aTitle.toLowerCase();
          if (!aTitle || aTitle.length < 3 || lowerTitle === 'more_vert' || lowerTitle.includes('more option') || lowerTitle === 'assignment' || lowerTitle === 'new announcement' || lowerTitle.includes('comment')) {
            continue;
          }

          const isMat = lower.includes('material') || lower.includes('support') || lower.includes('documentation') || lower.includes('séance');
          const type = isMat ? 'm' : 'a';
          const specificUrl = courseId ? '${CLASSROOM_URL}/u/${CLASSROOM_ACCOUNT_INDEX}/c/' + courseId + '/' + type + '/' + b64 + '/details' : pageUrl;

          if (!seenUrls.has(specificUrl)) {
            seenUrls.add(specificUrl);
            assignments.push({
              title: aTitle,
              stream_id: streamId,
              b64_id: b64,
              submission_status: subStatus,
              due_text: extractDueText(txt),
              url: specificUrl,
              raw: txt.substring(0, 300)
            });
          }
        }

        // 3. Scan assignment links (Classwork & To-do lists)
        const linkItems = Array.from(document.querySelectorAll('a[href*="/a/"], a[href*="/sa/"], a[href*="/m/"]'));
        for (const a of linkItems) {
          const href = a.href;
          if (!href || seenUrls.has(href)) continue;
          if (href.includes('/not-turned-in') || href.includes('/turned-in') || href.includes('/calendar/')) continue;
          if (!href.includes('/c/')) continue;
          seenUrls.add(href);

          const container = a.closest('[data-focus-id], [role="article"], div[role="listitem"], .j10ted, .asQXV') || a;
          const txt = container.innerText || '';
          const lower = txt.toLowerCase();

          let subStatus = 'UNKNOWN';
          if (lower.includes('annuler la remise') || lower.includes('remis') || lower.includes('turned in') || lower.includes('handed in') || lower.includes('noté') || lower.includes('graded')) {
            subStatus = 'SUBMITTED';
          } else if (lower.includes('attribué') || lower.includes('assigned') || lower.includes('manquant') || lower.includes('missing') || lower.includes('rendre')) {
            subStatus = 'NOT_SUBMITTED';
          }

          const headingEl = container.querySelector('h2, h3, [role="heading"], div[aria-level]');
          let cleanTitle = (headingEl ? headingEl.innerText.trim() : (a.innerText.trim() || txt.split('\\n')[0].trim()))
            .replace(/^assignment:?\\s*"?/i, '')
            .replace(/^material:?\\s*'?/i, '')
            .replace(/["']?\\s*$/i, '')
            .trim();

          const lowerTitle = cleanTitle.toLowerCase();
          if (!cleanTitle || cleanTitle.length < 3 || lowerTitle === 'more_vert' || lowerTitle.includes('more option') || lowerTitle === 'assignment' || lowerTitle === 'new announcement' || lowerTitle.includes('comment') || lowerTitle === 'to do' || lowerTitle === 'à faire' || lowerTitle.startsWith('post by')) {
            continue;
          }

          assignments.push({
            title: cleanTitle,
            submission_status: subStatus,
            due_text: extractDueText(txt),
            url: href,
            raw: txt.substring(0, 300)
          });
        }

        const links = Array.from(document.querySelectorAll('a[href]'))
          .map(a => ({ text: a.innerText.trim(), href: a.href }))
          .filter(l => l.href.includes('drive.google.com') || l.href.includes('classroom') || l.href.includes('docs.google'));

        return {
          title,
          url: pageUrl,
          currentSubmission,
          assignments,
          text: main.innerText.substring(0, 3500),
          links: links.slice(0, 15)
        };
      })()`;

      const data = await evaluatePage(p.webSocketDebuggerUrl, crScript);
      if (data) {
        results.classroom.push(data);
      }
    }

    // 2. Outlook Web
    else if (OUTLOOK_HOSTS.some(h => url.toLowerCase().includes(h))) {
      const olScript = `(() => {
        const title = document.title;
        const pageUrl = window.location.href;
        const items = Array.from(document.querySelectorAll('[role="listbox"] [role="option"]'));
        const emails = items.slice(0, 16).map(el => {
          const lines = el.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
          const clean = lines.filter(s => s.length > 2);
          let sender = lines[1] || lines[0] || '';
          let subject = clean.find(s => s !== sender && !s.startsWith('ATTENTION') && !s.startsWith('Bonjour') && !s.includes('@')) || '';
          return {
            sender: sender,
            subject: subject,
            raw: lines.join(' | ')
          };
        });
        return { title, url: pageUrl, emails };
      })()`;

      const data = await evaluatePage(p.webSocketDebuggerUrl, olScript);
      if (data) {
        results.outlook.push(data);
      }
    }
  }

  // 3. Background sync of Google Classroom official Done & Missing pages for authoritative submission status
  try {
    const verRes = await fetch(`${CDP_URL}/json/version`, { signal: AbortSignal.timeout(2000) });
    if (verRes.ok) {
      const ver = await verRes.json();
      if (ver.webSocketDebuggerUrl) {
        const bws = new WebSocket(ver.webSocketDebuggerUrl);
        let msgId = 1;
        const send = (ws, method, params = {}) => new Promise((resolve) => {
          const id = msgId++;
          const timer = setTimeout(() => resolve(null), 5000);
          const handler = (evt) => {
            try {
              const d = JSON.parse(evt.data);
              if (d.id === id) {
                clearTimeout(timer);
                ws.removeEventListener('message', handler);
                resolve(d.result);
              }
            } catch (e) {}
          };
          ws.addEventListener('message', handler);
          ws.send(JSON.stringify({ id, method, params }));
        });

        await new Promise((res, rej) => {
          const t = setTimeout(() => rej(new Error('ws timeout')), 3000);
          bws.onopen = () => { clearTimeout(t); res(); };
          bws.onerror = () => { clearTimeout(t); rej(new Error('ws err')); };
        });

        async function scrapeTodo(targetUrl, forcedStatus) {
          const created = await send(bws, 'Target.createTarget', { url: targetUrl, background: true });
          const targetId = created && created.targetId;
          if (!targetId) return [];
          let items = [];
          try {
            await new Promise(r => setTimeout(r, 700));
            const list = await (await fetch(`${CDP_URL}/json/list`, { signal: AbortSignal.timeout(2000) })).json();
            const pg = list.find(x => x.id === targetId);
            if (pg && pg.webSocketDebuggerUrl) {
              const pws = new WebSocket(pg.webSocketDebuggerUrl);
              await new Promise(r => { pws.onopen = r; setTimeout(r, 2000); });
              await send(pws, 'Page.enable');
              await send(pws, 'Emulation.setFocusEmulationEnabled', { enabled: true });
              await send(pws, 'Page.setWebLifecycleState', { state: 'active' });
              await new Promise(r => setTimeout(r, 3400));
              await send(pws, 'Runtime.evaluate', {
                expression: `(() => {
                  document.querySelectorAll('button[aria-expanded="false"], [role="button"][aria-expanded="false"]').forEach(b => b.click());
                })()`
              });
              await new Promise(r => setTimeout(r, 1000));
              const evalRes = await send(pws, 'Runtime.evaluate', {
                expression: `(() => {
                  const out = [];
                  const seen = new Set();
                  for (const a of document.querySelectorAll('a[href*="/c/"]')) {
                    if (!a.href.includes('/a/') && !a.href.includes('/sa/')) continue;
                    if (seen.has(a.href)) continue;
                    seen.add(a.href);
                    const row = a.closest('li, [role="listitem"], div[data-focus-id]') || a;
                    const lines = (row.innerText || a.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                    const cleanLines = lines.filter(l => !['assignment', 'live_help', 'handed in', 'done late', 'estigfend', 'not accepting work'].includes(l.toLowerCase()));
                    let title = cleanLines[0] || a.innerText.trim();
                    title = title.replace(/^assignment:?\\s*"?/i, '').replace(/["']$/i, '').trim();
                    const dueLine = lines.find(l => /^(?:date limite|échéance|due(?:\\s+date)?|aujourd'hui|demain|today|tomorrow|\\d{1,2}\\s+[a-zéû]+|[a-z]+\\s+\\d{1,2})/i.test(l)) || '';
                    const dueText = /^(aucune date limite|no due date)$/i.test(dueLine) ? '' : dueLine.replace(/^(?:date limite|échéance|due(?:\\s+date)?)\\s*[:\\-]?\\s*/i, '').trim();
                    out.push({
                      title,
                      url: a.href,
                      submission_status: '${forcedStatus}',
                      due_text: dueText,
                      is_missing: ${forcedStatus === 'NOT_SUBMITTED' ? 'true' : 'false'}
                    });
                  }
                  return out;
                })()`,
                returnByValue: true
              });
              items = (evalRes && evalRes.result && evalRes.result.value) ? evalRes.result.value : [];
              try { pws.close(); } catch (e) {}
            }
          } finally {
            await send(bws, 'Target.closeTarget', { targetId });
          }
          return items;
        }

        const turnedInUrl = `${CLASSROOM_URL}/u/${CLASSROOM_ACCOUNT_INDEX}/a/turned-in/all`;
        const missingUrl = `${CLASSROOM_URL}/u/${CLASSROOM_ACCOUNT_INDEX}/a/missing/all`;
        const doneList = await scrapeTodo(turnedInUrl, 'SUBMITTED');
        const missList = await scrapeTodo(missingUrl, 'NOT_SUBMITTED');

        if (doneList.length > 0 || missList.length > 0) {
          results.classroom.push({
            title: 'Classroom Global Submission Status Sync',
            url: turnedInUrl,
            is_status_sync: true,
            currentSubmission: null,
            assignments: [...doneList, ...missList],
            text: '',
            links: []
          });
        }
        try { bws.close(); } catch (e) {}
      }
    }
  } catch (e) {}

  console.log(JSON.stringify(results, null, 2));
}

harvest().catch(err => {
  console.log(JSON.stringify({ classroom: [], outlook: [], error: err.message }));
});
