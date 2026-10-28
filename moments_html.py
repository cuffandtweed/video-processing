"""Builds one self-contained web page (moments.html) from the moments and topics, for someone skimming to decide
where to look. No internet is needed to open it and nothing in it is sent anywhere."""
import json
import os
import re

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_STEM = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})\d{2}_(.+?)_\d{9,12}(?:_\d)?$")


def call_label(stem):
    """Readable pieces of a call's file stem: date, time, title, and a one-line "pretty" form."""
    m = _STEM.match(stem)
    if not m:
        return {"date": "", "time": "", "title": stem, "pretty": stem}
    year, month, day, hour, minute, raw_title = m.groups()
    title = raw_title.replace("_s_", "'s_").replace("_", " ").strip()
    title = " ".join(w[:1].upper() + w[1:] for w in title.split())
    h12 = int(hour) % 12 or 12
    ampm = "AM" if int(hour) < 12 else "PM"
    pretty = f"{MONTHS[int(month) - 1]} {int(day)} · {h12}:{minute} {ampm} · {title}"
    return {"date": f"{year}-{month}-{day}", "time": f"{hour}:{minute}", "title": title, "pretty": pretty}


def humanize_speakers(text):
    """"spk_1" becomes "Speaker 1": the raw labels mean nothing to someone who was not on the call."""
    return re.sub(r"\bspk_(\d+)", r"Speaker \1", text or "")


def script_safe_json(obj):
    """JSON that can sit inside a <script> block: a closing script tag in the data cannot end the block early."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def _local_base(base):
    """A folder as the start of a link: a Windows path becomes a file:/// address; a relative folder is kept
    (so a page sitting next to a "videos" folder works anywhere); always ends with a slash."""
    base = base.strip()
    if re.match(r"^[A-Za-z]:[\\/]", base):
        base = "file:///" + base.replace("\\", "/")
    elif base.startswith("\\\\"):                      # a network share
        base = "file:" + base.replace("\\", "/")
    base = base.replace("\\", "/")
    return base if base.endswith("/") or not base else base + "/"


def build_links(stems, mode, base=""):
    """{call stem: address of its recording}.

    drive: a Google Drive search for the exact file name (works for anyone with access to the folder; no file
           IDs needed, one extra click). local: the file inside a folder on the viewer's own computer. none: no links."""
    from urllib.parse import quote
    if mode == "drive":
        return {s: f"https://drive.google.com/drive/search?q={quote(s + '.mp4', safe='')}" for s in stems}
    if mode == "local":
        root = _local_base(base)
        return {s: f"{root}{quote(s + '.mp4', safe='')}" for s in stems}
    return {}


def normalize_people(people):
    """{name: rows} from tag_mentions, cut down to what the page needs, with each row's call stem added."""
    return {name: [{"stem": r["file"][:-4] if r["file"].endswith(".mp4") else r["file"], "time": r["time"],
                    "end": r.get("end", ""), "kind": r["kind"], "speaker": r.get("speaker", ""),
                    "matched": r["matched"], "snippet": r["snippet"]} for r in rows]
            for name, rows in (people or {}).items()}


_PEOPLE_TAB = '<button class="tab" data-tab="people">People</button>'


def build_html(moments, topics, links=None, link_label="Open recording", people=None):
    calls = {}
    for item in list(moments) + list(topics):
        calls.setdefault(item["call"], call_label(item["call"]))
    people = normalize_people(people)
    for rows in people.values():
        for r in rows:
            calls.setdefault(r["stem"], call_label(r["stem"]))
    clean_moments = [{**m, "why": humanize_speakers(m.get("why", "")), "quote": humanize_speakers(m.get("quote", ""))}
                     for m in moments]
    clean_topics = [{**t, "summary": humanize_speakers(t.get("summary", ""))} for t in topics]
    data = {"moments": clean_moments, "topics": clean_topics, "calls": calls,
            "links": links or {}, "linkLabel": link_label, "people": people}
    return (_TEMPLATE.replace("__PEOPLE_TAB__", _PEOPLE_TAB if people else "")
            .replace("__DATA__", script_safe_json(data)))


def write_html(out_dir, moments, topics, links=None, link_label="Open recording", filename="moments.html", people=None):
    """Write the page, and the data it was built from (moments.json, topics.json) so the page can be
    rebuilt later, for example for a different place the videos live, without asking the model again."""
    for name, obj in (("moments.json", moments), ("topics.json", topics)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    page = build_html(moments, topics, links, link_label, people)   # build first: a failure must not truncate an existing page
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


_LABELS = {"drive": "Open in Google Drive", "local": "Open local file"}


def rebuild(out_dir, mode="none", base="", filename="moments.html", people_path=None):
    """Rebuild a page from the saved moments.json and topics.json in out_dir, with links of the given kind.
    If people_path points at the people.json written by tag_mentions.py, the page gets a People tab and tags."""
    with open(os.path.join(out_dir, "moments.json"), encoding="utf-8") as f:
        moments = json.load(f)
    with open(os.path.join(out_dir, "topics.json"), encoding="utf-8") as f:
        topics = json.load(f)
    people = None
    if people_path and os.path.exists(people_path):
        with open(people_path, encoding="utf-8") as f:
            people = json.load(f)
    stems = {x["call"] for x in moments} | {x["call"] for x in topics}
    for rows in (people or {}).values():
        stems |= {r["file"][:-4] for r in rows}
    return write_html(out_dir, moments, topics, build_links(sorted(stems), mode, base),
                      _LABELS.get(mode, "Open recording"), filename, people)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Where to look: moments and topics</title>
<style>
  :root { --bg:#fafaf9; --card:#ffffff; --ink:#1c1917; --muted:#6b645c; --line:#e7e5e4; --accent:#1d4ed8; --chip:#f0eeec; }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) { --bg:#171513; --card:#211e1b; --ink:#f3efe9; --muted:#a39b91; --line:#37322d; --accent:#7aa2ff; --chip:#2c2824; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line); padding:14px 16px 10px; }
  .wrap { max-width:880px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { margin:0 0 10px; color:var(--muted); font-size:14px; }
  nav button.tab { font:inherit; font-weight:600; border:0; background:none; color:var(--muted); padding:6px 14px 8px; cursor:pointer; border-bottom:3px solid transparent; }
  nav button.tab.on { color:var(--ink); border-bottom-color:var(--accent); }
  .bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:10px; }
  input[type=search], select { font:inherit; padding:6px 10px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--ink); }
  input[type=search] { flex:1 1 200px; min-width:140px; }
  .chip { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--chip); color:var(--ink); border-radius:999px; padding:3px 11px; cursor:pointer; }
  .chip.off { opacity:.45; text-decoration:line-through; }
  .btn { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--card); color:var(--ink); border-radius:8px; padding:5px 10px; cursor:pointer; text-decoration:none; display:inline-block; }
  a.btn.go { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
  main { max-width:880px; margin:0 auto; padding:14px 16px 60px; }
  .count { color:var(--muted); font-size:14px; margin:4px 0 12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin:0 0 12px; }
  .top { display:flex; align-items:center; gap:10px; margin-bottom:6px; }
  .tag { font-size:12px; font-weight:700; letter-spacing:.03em; text-transform:uppercase; padding:2px 9px; border-radius:999px; color:#fff; }
  .t-emotional{background:#c2410c} .t-disagreement{background:#b91c1c} .t-laughter{background:#0f766e} .t-breakthrough{background:#6d28d9}
  .t-humor{background:#a16207} .t-tension{background:#be185d} .t-silence{background:#475569} .t-away{background:#78716c} .t-other{background:#57534e}
  .dots { letter-spacing:2px; color:var(--accent); font-size:15px; }
  .star { margin-left:auto; font-size:22px; line-height:1; border:0; background:none; cursor:pointer; color:var(--muted); }
  .star.on { color:#d97706; }
  blockquote { margin:2px 0 6px; font-size:19px; line-height:1.45; }
  .why { margin:0 0 8px; color:var(--muted); font-size:14px; }
  .meta { font-size:13px; color:var(--muted); display:flex; flex-wrap:wrap; gap:6px 12px; align-items:center; }
  .meta b { color:var(--ink); font-weight:600; }
  code { font:13px ui-monospace, Consolas, monospace; background:var(--chip); padding:1px 6px; border-radius:5px; }
  .warn { color:#b45309; font-size:13px; }
  details.topic { background:var(--card); border:1px solid var(--line); border-radius:12px; margin:0 0 10px; }
  details.topic > summary { cursor:pointer; padding:12px 16px; font-weight:600; list-style:none; display:flex; gap:10px; align-items:baseline; }
  details.topic > summary::-webkit-details-marker { display:none; }
  details.topic > summary::before { content:"▸"; color:var(--muted); }
  details.topic[open] > summary::before { content:"▾"; }
  .n { color:var(--muted); font-weight:400; font-size:14px; }
  .mention { padding:10px 16px; border-top:1px solid var(--line); }
  .mention p { margin:0 0 4px; }
  .empty { color:var(--muted); padding:30px 0; text-align:center; }
  .ptag { font-size:12px; font-weight:600; color:var(--accent); border:1px solid var(--accent); border-radius:999px; padding:1px 9px; }
  mark { background:#fde68a; color:#1c1917; border-radius:3px; padding:0 2px; }
  details.inner { margin:0; border:0; border-top:1px solid var(--line); border-radius:0; background:transparent; }
  details.inner > summary { padding:9px 16px 9px 30px; font-weight:500; }
  .kind { font-style:italic; }
</style>
</head>
<body>
<header><div class="wrap">
  <h1>Where to look</h1>
  <p class="sub" id="sub"></p>
  <nav><button class="tab on" data-tab="moments">Moments</button><button class="tab" data-tab="topics">Topics</button>__PEOPLE_TAB__</nav>
  <div class="bar" id="bar"></div>
</div></header>
<main id="app"></main>

<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const TYPES = ['emotional','disagreement','laughter','breakthrough','humor','tension','silence'];
const state = { tab:'moments', q:'', off:new Set(), min:4, call:'', shown:60, stars:new Set(), topicQ:'', person:'', peopleQ:'' };
try { state.stars = new Set(JSON.parse(localStorage.getItem('picks') || '[]')); } catch (e) {}
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = stem => (D.calls[stem] || {pretty: stem}).pretty;
const key = m => m.call + '|' + m.timestamp + '|' + m.type;
const savePicks = () => { try { localStorage.setItem('picks', JSON.stringify([...state.stars])); } catch (e) {} };
const dots = n => '●'.repeat(n) + '○'.repeat(5 - n);
const openLink = stem => D.links[stem]
  ? `<a class="btn go" href="${esc(D.links[stem])}" target="_blank" rel="noopener">${esc(D.linkLabel)}</a>` : '';

// People tags: who is named, where. A moment is tagged when someone says that person's name within a minute of it.
const PEOPLE = Object.keys(D.people || {});
const secs = c => { const p = String(c).split(':').map(Number); return p.length === 3 && p.every(n => !isNaN(n)) ? p[0] * 3600 + p[1] * 60 + p[2] : NaN; };
const spokenBy = {};
PEOPLE.forEach(n => D.people[n].forEach(r => { if (r.kind === 'spoken') (spokenBy[r.stem] = spokenBy[r.stem] || []).push({name: n, t: secs(r.time)}); }));
const personTags = m => { const t = secs(m.timestamp), seen = new Set(); (spokenBy[m.call] || []).forEach(x => { if (Math.abs(x.t - t) <= 60) seen.add(x.name); }); return [...seen]; };
const tagHtml = m => personTags(m).map(n => `<span class="ptag" title="${esc(n)} is named within a minute of this moment">${esc(n)}</span>`).join('');
const hl = (text, matched) => {
  let h = esc(text);
  String(matched).split(' / ').forEach(w => { if (!w) return; h = h.replace(new RegExp(esc(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), s => `<mark>${s}</mark>`); });
  return h;
};

function copyText(t, btn) {
  const done = () => { const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => btn.textContent = o, 1200); };
  if (navigator.clipboard) navigator.clipboard.writeText(t).then(done, done); else done();
}
window.copyText = copyText;

function matchesQ(text, q) { return !q || text.toLowerCase().includes(q.toLowerCase()); }

function filteredMoments() {
  return D.moments.filter(m => m.score >= state.min && !state.off.has(m.type) && (!state.call || m.call === state.call)
      && (!state.person || personTags(m).includes(state.person))
      && matchesQ([m.quote, m.why, pretty(m.call), m.type].join(' '), state.q))
    .sort((a, b) => b.score - a.score || a.call.localeCompare(b.call) || a.timestamp.localeCompare(b.timestamp));
}

function momentCard(m) {
  const starred = state.stars.has(key(m));
  const warn = m.verbatim === 'NO' ? '<span class="warn">⚠ check the wording against the recording</span>' : '';
  return `<article class="card">
    <div class="top"><span class="tag t-${esc(m.type)}">${esc(m.type)}</span><span class="dots" title="strength ${m.score} of 5">${dots(m.score)}</span>${tagHtml(m)}
      <button class="star ${starred ? 'on' : ''}" data-key="${esc(key(m))}" title="Add to my picks">${starred ? '★' : '☆'}</button></div>
    <blockquote>${esc(m.quote)}</blockquote>
    <p class="why">${esc(m.why)} ${warn}</p>
    <div class="meta"><b>${esc(pretty(m.call))}</b><span>at <code>${esc(m.timestamp)}</code></span>
      ${openLink(m.call)}
      <span>file <code>${esc(m.call)}.mp4</code> <button class="btn" onclick="copyText('${esc(m.call)}.mp4 @ ${esc(m.timestamp)}', this)">Copy</button></span></div>
  </article>`;
}

function renderMoments() {
  const all = filteredMoments(), list = all.slice(0, state.shown);
  const body = list.length ? list.map(momentCard).join('') : '<p class="empty">Nothing matches these filters.</p>';
  const more = all.length > list.length ? `<p style="text-align:center"><button class="btn" id="more">Show more (${all.length - list.length} left)</button></p>` : '';
  document.getElementById('app').innerHTML = `<p class="count">${all.length} moment${all.length === 1 ? '' : 's'} · ${state.stars.size} in my picks</p>${body}${more}`;
}

function renderTopics() {
  const by = {};
  D.topics.filter(t => matchesQ([t.canonical, t.topic, t.summary, pretty(t.call)].join(' '), state.topicQ))
    .forEach(t => (by[t.canonical] = by[t.canonical] || []).push(t));
  const names = Object.keys(by).sort((a, b) => new Set(by[b].map(t => t.call)).size - new Set(by[a].map(t => t.call)).size || a.localeCompare(b));
  const html = names.map(n => {
    const items = by[n].sort((a, b) => a.call.localeCompare(b.call) || String(a.start).localeCompare(String(b.start)));
    const calls = new Set(items.map(t => t.call)).size;
    const rows = items.map(t => `<div class="mention"><p><b>${esc(t.topic)}</b> — ${esc(t.summary)}</p><div class="meta"><b>${esc(pretty(t.call))}</b>
      <span><code>${esc(t.start)}</code>–<code>${esc(t.end)}</code></span>${openLink(t.call)}<span>file <code>${esc(t.call)}.mp4</code></span></div></div>`).join('');
    return `<details class="topic"><summary>${esc(n)} <span class="n">${items.length} mention${items.length === 1 ? '' : 's'} in ${calls} call${calls === 1 ? '' : 's'}</span></summary>${rows}</details>`;
  }).join('');
  document.getElementById('app').innerHTML = `<p class="count">${names.length} topics</p>${html || '<p class="empty">No topics match.</p>'}`;
}

function mentionRow(r) {
  const when = r.end ? `<code>${esc(r.time)}</code>–<code>${esc(r.end)}</code>` : `<code>${esc(r.time)}</code>`;
  const label = r.kind === 'spoken' ? 'said' + (r.speaker ? ' by ' + esc(r.speaker) : '') : 'in the video summary';
  return `<div class="mention"><p>${hl(r.snippet, r.matched)}</p><div class="meta"><span>${when}</span><span class="kind">${label}</span></div></div>`;
}

function renderPeople() {
  const q = state.peopleQ.toLowerCase();
  const html = PEOPLE.map(name => {
    const rows = D.people[name].filter(r => !q || [name, r.snippet, pretty(r.stem)].join(' ').toLowerCase().includes(q));
    const byCall = {};
    rows.forEach(r => (byCall[r.stem] = byCall[r.stem] || []).push(r));
    const stems = Object.keys(byCall).sort();
    const spokenRows = rows.filter(r => r.kind === 'spoken');
    const spokenCalls = new Set(spokenRows.map(r => r.stem)).size;
    const calls = stems.map(s => {
      const items = byCall[s].sort((a, b) => a.time.localeCompare(b.time) || (a.kind === 'spoken' ? -1 : 1));
      const said = items.filter(r => r.kind === 'spoken').length, inSummary = items.length - said;
      const counts = [said ? said + ' said' : '', inSummary ? inSummary + ' in the video summary' : ''].filter(Boolean).join(' · ');
      return `<details class="topic inner"><summary>${esc(pretty(s))} <span class="n">${counts}</span></summary>
        <div class="mention"><div class="meta">${openLink(s)}<span>file <code>${esc(s)}.mp4</code></span></div></div>${items.map(mentionRow).join('')}</details>`;
    }).join('');
    return `<details class="topic"><summary>${esc(name)} <span class="n">${spokenRows.length} said in ${spokenCalls} call${spokenCalls === 1 ? '' : 's'}
      · ${rows.length - spokenRows.length} in the video summary · ${stems.length} call${stems.length === 1 ? '' : 's'} in all</span></summary>${calls || '<p class="empty">No matches.</p>'}</details>`;
  }).join('');
  document.getElementById('app').innerHTML = `<p class="count">${PEOPLE.length} ${PEOPLE.length === 1 ? 'person' : 'people'} tagged. Matches are exact names, including the odd spellings speech-to-text used for them.</p>${html}`;
}

function renderBar() {
  const bar = document.getElementById('bar');
  if (state.tab === 'people') {
    bar.innerHTML = `<input type="search" id="pq" placeholder="Search people, calls or quotes" value="${esc(state.peopleQ)}">`;
    document.getElementById('pq').oninput = e => { state.peopleQ = e.target.value; renderPeople(); };
    return;
  }
  if (state.tab === 'topics') {
    bar.innerHTML = `<input type="search" id="tq" placeholder="Search topics" value="${esc(state.topicQ)}">`;
    document.getElementById('tq').oninput = e => { state.topicQ = e.target.value; renderTopics(); };
    return;
  }
  const callOpts = Object.keys(D.calls).sort().map(c => `<option value="${esc(c)}" ${state.call === c ? 'selected' : ''}>${esc(pretty(c))}</option>`).join('');
  bar.innerHTML = `<input type="search" id="q" placeholder="Search quotes and reasons" value="${esc(state.q)}">
    <select id="min" title="Minimum strength">${[1,2,3,4,5].map(n => `<option value="${n}" ${state.min === n ? 'selected' : ''}>${n}+ strength</option>`).join('')}</select>
    <select id="call"><option value="">All calls</option>${callOpts}</select>
    ${PEOPLE.length ? `<select id="person" title="Only moments where this person is named within a minute"><option value="">Anyone named</option>${PEOPLE.map(n => `<option value="${esc(n)}" ${state.person === n ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select>` : ''}
    ${TYPES.map(t => `<button class="chip ${state.off.has(t) ? 'off' : ''}" data-type="${t}">${t}</button>`).join('')}
    <button class="btn" id="dl">Download my picks</button>`;
  document.getElementById('q').oninput = e => { state.q = e.target.value; state.shown = 60; renderMoments(); wire(); };
  document.getElementById('min').onchange = e => { state.min = +e.target.value; state.shown = 60; renderMoments(); wire(); };
  document.getElementById('call').onchange = e => { state.call = e.target.value; state.shown = 60; renderMoments(); wire(); };
  const ps = document.getElementById('person');
  if (ps) ps.onchange = e => { state.person = e.target.value; state.shown = 60; renderMoments(); wire(); };
  bar.querySelectorAll('.chip').forEach(b => b.onclick = () => { const t = b.dataset.type; state.off.has(t) ? state.off.delete(t) : state.off.add(t); b.classList.toggle('off'); state.shown = 60; renderMoments(); wire(); });
  document.getElementById('dl').onclick = downloadPicks;
}

function csvCell(v) { return '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"'; }
function downloadPicks() {
  const picks = D.moments.filter(m => state.stars.has(key(m)));
  if (!picks.length) { alert('Star some moments first (the ☆ on each card).'); return; }
  const rows = [['call','file','timestamp','type','strength','quote','why']].concat(picks.map(m =>
    [pretty(m.call), m.call + '.mp4', m.timestamp, m.type, m.score, m.quote, m.why]));
  const blob = new Blob([rows.map(r => r.map(csvCell).join(',')).join('\r\n')], {type:'text/csv'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'my-picks.csv'; a.click();
}

function wire() {
  document.querySelectorAll('.star').forEach(b => b.onclick = () => {
    const k = b.dataset.key; state.stars.has(k) ? state.stars.delete(k) : state.stars.add(k); savePicks();
    b.classList.toggle('on'); b.textContent = state.stars.has(k) ? '★' : '☆';
    const c = document.querySelector('.count'); if (c) c.textContent = c.textContent.replace(/\d+ in my picks/, state.stars.size + ' in my picks');
  });
  const more = document.getElementById('more'); if (more) more.onclick = () => { state.shown += 60; renderMoments(); wire(); };
}

function show(tab) {
  state.tab = tab;
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  renderBar();
  if (tab === 'moments') { renderMoments(); wire(); } else if (tab === 'people') renderPeople(); else renderTopics();
}
document.querySelectorAll('.tab').forEach(b => b.onclick = () => show(b.dataset.tab));
document.getElementById('sub').textContent = `${new Set(D.moments.map(m => m.call)).size} calls · ${D.moments.length} moments · ${new Set(D.topics.map(t => t.canonical)).size} topics${PEOPLE.length ? ' · ' + PEOPLE.length + ' ' + (PEOPLE.length === 1 ? 'person' : 'people') + ' tagged' : ''}. Names of speakers are not reliable, so they are shown as Speaker 1, 2 and so on.`;
show('moments');
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Rebuild the moments page from saved data, with links to the recordings.")
    ap.add_argument("out_dir", nargs="?", default="moments_proto")
    ap.add_argument("--links", choices=["none", "drive", "local"], default="none",
                    help="drive = Google Drive search links; local = files in a folder on the viewer's computer")
    ap.add_argument("--base", default="", help="for --links local: the folder holding the videos (a path, or a relative folder)")
    ap.add_argument("--name", default="moments.html", help="file name for the page")
    ap.add_argument("--people", default=os.path.join("mentions", "people.json"),
                    help="people.json from tag_mentions.py; if it exists the page gets a People tab (default: mentions/people.json)")
    a = ap.parse_args()
    print(rebuild(a.out_dir, a.links, a.base, a.name, a.people))
