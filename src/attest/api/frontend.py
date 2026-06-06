"""The web front-end Attest serves at ``GET /``.

Primary surface: the **React disclosure-drafting workspace** (source in ``web/``),
built into a single self-contained ``index.html`` and shipped at
``api/static/index.html``. ``attest serve`` serves it at ``/`` and the workspace
reconciles each figure edit against the live deterministic engine via the
same-origin ``/tenants/{tenant}/verify`` endpoint (baked in at build time with
``VITE_ATTEST_API=/``).

Fallback: if that built bundle isn't present (e.g. a source checkout that hasn't
run the web build), ``index_html`` returns a minimal, dependency-free page —
vanilla JS, inline CSS — that drives the same public API
(``/verify-close-pack``, ``/override``, ``/sign-off``, ``/audit``) so the server
is still usable in a browser without the Node toolchain.
"""

from __future__ import annotations

import json
from pathlib import Path

from attest.demo import TENANT, build_documents

_SPA_PATH = Path(__file__).parent / "static" / "index.html"

# The reference close pack is embedded into the fallback page so the browser can
# POST it to /verify-close-pack on load.
_DOCUMENTS_JSON = "[" + ",".join(d.model_dump_json() for d in build_documents()) + "]"


def has_spa() -> bool:
    """True when the built React workspace bundle is available to serve."""
    return _SPA_PATH.is_file()


def spa_html() -> str:
    """The built React workspace if shipped, else the minimal fallback page."""
    if has_spa():
        return _SPA_PATH.read_text(encoding="utf-8")
    return index_html()


def index_html() -> str:
    """The minimal fallback front-end, with the reference close pack injected."""
    return _PAGE.replace("__TENANT__", json.dumps(TENANT)).replace(
        "__DOCUMENTS__", _DOCUMENTS_JSON
    )


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Attest — disclosure verification</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --panel2:#1d212b; --line:#2a2f3a;
    --ink:#e7eaf0; --muted:#9aa3b2; --accent:#6ea8fe;
    --traced:#37b679; --review:#e0a106; --conflict:#e5484d; --untraced:#8b93a1;
    --block:#e5484d; --warn:#e0a106; --info:#6ea8fe;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  header { padding:20px 28px; border-bottom:1px solid var(--line);
    display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  header h1 { font-size:18px; margin:0; letter-spacing:.2px; }
  header .sub { color:var(--muted); font-size:13px; }
  header .spacer { flex:1; }
  .controls { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  label.actor { color:var(--muted); font-size:12px; display:flex; gap:6px; align-items:center; }
  input[type=text]{ background:var(--panel2); border:1px solid var(--line); color:var(--ink);
    border-radius:6px; padding:6px 9px; font-size:13px; min-width:200px; }
  button { background:var(--accent); color:#0b1220; border:0; border-radius:6px;
    padding:8px 14px; font-weight:600; cursor:pointer; font-size:13px; }
  button.ghost { background:transparent; color:var(--ink); border:1px solid var(--line); }
  button:hover { filter:brightness(1.07); }
  button:disabled { opacity:.5; cursor:default; }
  main { padding:24px 28px; max-width:1100px; margin:0 auto; }
  .banner { border-radius:10px; padding:14px 18px; margin-bottom:22px; font-weight:600;
    border:1px solid var(--line); display:flex; align-items:center; gap:12px; }
  .banner.ok { background:rgba(55,182,121,.12); border-color:rgba(55,182,121,.4); }
  .banner.bad { background:rgba(229,72,77,.12); border-color:rgba(229,72,77,.4); }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
  .ok .dot{ background:var(--traced);} .bad .dot{ background:var(--conflict);}
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    margin-bottom:20px; overflow:hidden; }
  .card > .head { padding:14px 18px; border-bottom:1px solid var(--line);
    display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .card > .head h2 { font-size:15px; margin:0; }
  .card > .head .spacer { flex:1; }
  .counts { display:flex; gap:8px; flex-wrap:wrap; }
  .pill { font-size:11px; padding:2px 9px; border-radius:999px; font-weight:700;
    text-transform:uppercase; letter-spacing:.4px; border:1px solid transparent; }
  .pill.traced{ color:var(--traced); border-color:var(--traced);}
  .pill.needs_review{ color:var(--review); border-color:var(--review);}
  .pill.conflict{ color:var(--conflict); border-color:var(--conflict);}
  .pill.untraced{ color:var(--untraced); border-color:var(--untraced);}
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:10px 18px; border-bottom:1px solid var(--line);
    vertical-align:top; }
  th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.5px; }
  tr:last-child td { border-bottom:0; }
  .badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:6px;
    text-transform:uppercase; letter-spacing:.4px; white-space:nowrap; }
  .badge.traced{ background:rgba(55,182,121,.16); color:var(--traced);}
  .badge.needs_review{ background:rgba(224,161,6,.16); color:var(--review);}
  .badge.conflict{ background:rgba(229,72,77,.16); color:var(--conflict);}
  .badge.untraced{ background:rgba(139,147,161,.16); color:var(--untraced);}
  .metric { font-weight:600; }
  .reason { color:var(--muted); font-size:13px; }
  .figure { font-variant-numeric:tabular-nums; }
  .findings { padding:6px 18px 14px; }
  .finding { display:flex; gap:10px; align-items:flex-start; padding:8px 0;
    border-top:1px dashed var(--line); }
  .finding:first-child{ border-top:0; }
  .sev { font-size:10px; font-weight:800; padding:2px 7px; border-radius:5px; margin-top:1px;
    text-transform:uppercase; letter-spacing:.4px; }
  .sev.block{ background:rgba(229,72,77,.18); color:var(--block);}
  .sev.warn{ background:rgba(224,161,6,.18); color:var(--warn);}
  .sev.info{ background:rgba(110,168,254,.18); color:var(--info);}
  .finding .body .rule{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
    color:var(--muted);}
  .audit td { font-size:13px; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .chip { font-size:11px; padding:2px 8px; border-radius:6px; border:1px solid var(--line);
    color:var(--muted); }
  .right { text-align:right; }
  .muted { color:var(--muted); }
  .err { background:rgba(229,72,77,.12); border:1px solid rgba(229,72,77,.4);
    color:#ffb4b6; padding:12px 16px; border-radius:8px; margin-bottom:18px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; white-space:pre-wrap; }
  .rowbtn { background:transparent; border:1px solid var(--line); color:var(--ink);
    padding:4px 9px; font-size:12px; font-weight:600; }
  footer { color:var(--muted); font-size:12px; padding:8px 28px 40px; max-width:1100px; margin:0 auto; }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>Attest</h1>
  <span class="sub">deterministic disclosure-verification spine · tenant <span class="mono" id="tenant"></span></span>
  <span class="spacer"></span>
  <div class="controls">
    <label class="actor">actor
      <input type="text" id="actor" value="max@deerpark.io" />
    </label>
    <button id="run">Re-run verification</button>
    <a href="/docs" target="_blank"><button class="ghost">API docs</button></a>
  </div>
</header>
<main>
  <div id="error" class="err" style="display:none"></div>
  <div id="banner" class="banner"><span class="dot"></span><span id="banner-text">Loading…</span></div>
  <div id="docs"></div>
  <div id="consistency"></div>
  <div id="audit"></div>
</main>
<footer>
  Every figure is tied out against filed XBRL facts; verdicts, overrides and
  sign-offs are immutable, hash-chained audit events. No model is in this loop —
  the core is deterministic by design.
</footer>

<script>
const TENANT = __TENANT__;
const DOCUMENTS = __DOCUMENTS__;
const $ = (s, r=document) => r.querySelector(s);
const el = (t, c, txt) => { const e=document.createElement(t); if(c)e.className=c; if(txt!=null)e.textContent=txt; return e; };
document.getElementById('tenant').textContent = TENANT;

async function api(method, path, body){
  const r = await fetch(path, {
    method,
    headers: body!=null ? {'content-type':'application/json'} : {},
    body: body!=null ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if(!r.ok){ const e = new Error(typeof data==='string'?data:JSON.stringify(data,null,2)); e.status=r.status; throw e; }
  return data;
}

function showError(msg){ const e=$('#error'); e.style.display='block'; e.textContent='Request failed:\n'+msg; }
function clearError(){ $('#error').style.display='none'; }

function actor(){ return ($('#actor').value || 'anonymous').trim(); }

function renderBanner(publishable){
  const b=$('#banner'); const t=$('#banner-text');
  b.className = 'banner ' + (publishable?'ok':'bad');
  t.textContent = publishable
    ? 'Close pack is publishable — every figure traced and all rules satisfied.'
    : 'Close pack is NOT publishable — unresolved conflicts or rule blocks below.';
}

function countsRow(c){
  const wrap = el('div','counts');
  for(const k of ['traced','needs_review','conflict','untraced']){
    if(!c[k]) continue;
    wrap.appendChild(el('span','pill '+k, k.replace('_',' ')+' · '+c[k]));
  }
  return wrap;
}

function verdictsTable(doc){
  const table = el('table');
  const thead = el('thead');
  const htr = el('tr');
  ['Verdict','Metric','As written','Source','Why',''].forEach(h=>htr.appendChild(el('th',null,h)));
  thead.appendChild(htr); table.appendChild(thead);
  const tb = el('tbody');
  for(const v of doc.verdicts){
    const tr = el('tr');
    const c1 = el('td'); c1.appendChild(el('span','badge '+v.verdict, v.verdict.replace('_',' '))); tr.appendChild(c1);
    tr.appendChild(el('td','metric', v.metric));
    tr.appendChild(el('td','figure', v.displayed_text));
    tr.appendChild(el('td','figure muted', v.source_value!=null ? v.source_value : '—'));
    tr.appendChild(el('td','reason', v.reason));
    const c6 = el('td','right');
    if(v.verdict==='conflict' || v.verdict==='needs_review'){
      const btn = el('button','rowbtn', v.verdict==='conflict'?'Override':'Accept');
      btn.onclick = ()=>doOverride(v.claim_id, v.metric);
      c6.appendChild(btn);
    }
    tr.appendChild(c6);
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  return table;
}

function findingsBlock(doc){
  if(!doc.findings || !doc.findings.length) return null;
  const wrap = el('div','findings');
  for(const f of doc.findings){
    const row = el('div','finding');
    row.appendChild(el('span','sev '+f.severity, f.severity));
    const body = el('div','body');
    body.appendChild(el('div',null,f.message));
    body.appendChild(el('div','rule',f.rule));
    row.appendChild(body);
    wrap.appendChild(row);
  }
  return wrap;
}

function renderDocs(pack){
  const root = $('#docs'); root.innerHTML='';
  for(const doc of pack.documents){
    const meta = DOCUMENTS.find(d=>d.id===doc.document_id) || {};
    const card = el('div','card');
    const head = el('div','head');
    head.appendChild(el('h2', null, meta.title || doc.document_id));
    head.appendChild(el('span','chip', (meta.kind||'document')));
    head.appendChild(el('div','spacer'));
    head.appendChild(countsRow(doc.counts));
    const pub = el('span','badge '+(doc.publishable?'traced':'conflict'),
                   doc.publishable?'publishable':'blocked');
    head.appendChild(pub);
    const sign = el('button','rowbtn','Sign off');
    sign.onclick = ()=>doSignOff(doc.document_id);
    head.appendChild(sign);
    card.appendChild(head);
    card.appendChild(verdictsTable(doc));
    const fb = findingsBlock(doc);
    if(fb) card.appendChild(fb);
    root.appendChild(card);
  }
}

function renderConsistency(pack){
  const root = $('#consistency'); root.innerHTML='';
  const card = el('div','card');
  const head = el('div','head');
  head.appendChild(el('h2',null,'Cross-document consistency'));
  card.appendChild(head);
  if(!pack.consistency_findings.length){
    const ok = el('div','findings');
    const row = el('div','finding');
    row.appendChild(el('span','sev info','ok'));
    row.appendChild(el('div','body','All shared figures agree across the release, script, and Q&A.'));
    ok.appendChild(row);
    card.appendChild(ok);
  } else {
    card.appendChild(findingsBlock({findings: pack.consistency_findings}));
  }
  root.appendChild(card);
}

async function renderAudit(){
  const root = $('#audit');
  const chain = await api('GET','/audit/verify');
  const events = await api('GET','/tenants/'+encodeURIComponent(TENANT)+'/audit');
  root.innerHTML='';
  const card = el('div','card audit');
  const head = el('div','head');
  head.appendChild(el('h2',null,'Audit trail'));
  head.appendChild(el('div','spacer'));
  head.appendChild(el('span','chip', events.length+' events'));
  head.appendChild(el('span','badge '+(chain.intact?'traced':'conflict'),
                     chain.intact?'chain intact':'chain broken'));
  card.appendChild(head);
  const table = el('table');
  const thead = el('thead'); const htr=el('tr');
  ['#','Event','Actor','Detail'].forEach(h=>htr.appendChild(el('th',null,h)));
  thead.appendChild(htr); table.appendChild(thead);
  const tb = el('tbody');
  for(const ev of events){
    const tr = el('tr');
    tr.appendChild(el('td','mono muted', String(ev.seq)));
    tr.appendChild(el('td',null, ev.type));
    tr.appendChild(el('td','mono', ev.actor));
    const p = ev.payload || {};
    const detail = p.claim_id ? ('claim '+p.claim_id+' — '+(p.justification||''))
                 : p.document_id ? ('document '+p.document_id+(p.scope?(' ('+p.scope+')'):''))
                 : p.ingested!=null ? (p.ingested+' facts from '+(p.source||'source'))
                 : '';
    tr.appendChild(el('td','muted', detail));
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  card.appendChild(table);
  root.appendChild(card);
}

async function runVerification(){
  clearError();
  $('#run').disabled = true;
  try {
    const pack = await api('POST','/tenants/'+encodeURIComponent(TENANT)+'/verify-close-pack', DOCUMENTS);
    renderBanner(pack.publishable);
    renderDocs(pack);
    renderConsistency(pack);
    await renderAudit();
  } catch(e){
    showError((e.status?('HTTP '+e.status+'\n'):'')+e.message);
  } finally {
    $('#run').disabled = false;
  }
}

async function doOverride(claimId, metric){
  const why = prompt('Justification for overriding "'+metric+'" ('+claimId+'):',
                     'Reviewed against the filed source; accepting with sign-off.');
  if(why==null) return;
  try { await api('POST','/tenants/'+encodeURIComponent(TENANT)+'/override',
        {actor:actor(), claim_id:claimId, justification:why});
        await renderAudit();
  } catch(e){ showError(e.message); }
}

async function doSignOff(documentId){
  try { await api('POST','/tenants/'+encodeURIComponent(TENANT)+'/documents/'+encodeURIComponent(documentId)+'/sign-off',
        {actor:actor(), scope:'document'});
        await renderAudit();
  } catch(e){ showError(e.message); }
}

$('#run').onclick = runVerification;
runVerification();
</script>
</body>
</html>
"""
