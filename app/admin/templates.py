"""Jinja2 templates for the admin panel (DictLoader — the panel is a handful of pages).

The knowledge-base editor serializes its dynamic tables to JSON in one hidden field
client-side, so the server parses a single structured payload instead of hundreds of
URL-encoded params (the old bot needed a 5mb/10000-param body limit for Bangla forms).
"""

from jinja2 import DictLoader, Environment, select_autoescape

BASE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} — De Jure Bot</title>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; background: #f4f5f7; color: #1a1a2e; }
header { background: #1a1a2e; color: #fff; padding: 12px 24px; display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
header h1 { font-size: 17px; margin: 0 24px 0 0; }
header a { color: #cfd3ff; text-decoration: none; font-size: 14px; }
header a.active, header a:hover { color: #fff; }
header .spacer { flex: 1; }
header form { margin: 0; }
main { max-width: 1100px; margin: 24px auto; padding: 0 16px 64px; }
.card { background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08); padding: 20px; margin-bottom: 20px; }
.card h2 { margin-top: 0; font-size: 16px; }
label { display: block; font-weight: 600; font-size: 13px; margin: 12px 0 4px; }
input[type=text], input[type=password], input[type=number], textarea, select {
  width: 100%; padding: 8px 10px; border: 1px solid #c9ccd4; border-radius: 6px; font: inherit; font-size: 14px; }
textarea { min-height: 110px; resize: vertical; }
button { background: #3b4ce2; color: #fff; border: 0; border-radius: 6px; padding: 9px 18px; font: inherit; cursor: pointer; }
button.small { padding: 4px 10px; font-size: 12px; }
button.danger { background: #c0392b; }
button.ghost { background: #e8eaf3; color: #1a1a2e; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border: 1px solid #e1e3ea; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f0f1f6; }
td input, td textarea { border: 1px solid transparent; background: transparent; padding: 4px; width: 100%; min-width: 70px; }
td input:focus, td textarea:focus { border-color: #3b4ce2; background: #fff; outline: none; }
.msg { padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
.msg.ok { background: #e6f7ec; color: #14713d; }
.msg.err { background: #fdeaea; color: #b03030; }
.muted { color: #777; font-size: 12px; }
.scroll { max-height: 420px; overflow-y: auto; }
.searchbox { margin-bottom: 10px; }
.row-actions { white-space: nowrap; }
</style>
</head>
<body>
{% if user %}
<header>
  <h1>De Jure Bot</h1>
  {% if role == "admin" %}<a href="/" {% if page == "kb" %}class="active"{% endif %}>Knowledge base</a>
  <a href="/system-prompt" {% if page == "prompt" %}class="active"{% endif %}>Bot behaviour</a>
  <a href="/leads" {% if page == "leads" %}class="active"{% endif %}>Leads</a>
  {% else %}<a href="/" {% if page == "kb" %}class="active"{% endif %}>Courses &amp; products</a>{% endif %}
  <a href="/blocked" {% if page == "blocked" %}class="active"{% endif %}>Blocked users</a>
  <span class="spacer"></span>
  <span class="muted" style="color:#aab">{{ user }} ({{ role }})</span>
  <form method="post" action="/logout"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="ghost small">Log out</button></form>
</header>
{% endif %}
<main>
{% if saved %}<div class="msg ok">Saved. The bot is now using the new data.</div>{% endif %}
{% if error %}<div class="msg err">{{ error }}</div>{% endif %}
{% block content %}{% endblock %}
</main>
</body>
</html>"""

LOGIN = """{% extends "base" %}{% block content %}
<div class="card" style="max-width:380px;margin:60px auto">
<h2>Admin login</h2>
<form method="post" action="/login">
  <label>Username</label><input type="text" name="username" autofocus>
  <label>Password</label><input type="password" name="password">
  <div style="margin-top:16px"><button>Log in</button></div>
</form>
</div>
{% endblock %}"""

KB = """{% extends "base" %}{% block content %}
<form method="post" action="/save" id="kbform">
<input type="hidden" name="_csrf" value="{{ csrf }}">
<input type="hidden" name="kb_json" id="kb_json">

{% if role == "admin" %}
<div class="card"><h2>About</h2><textarea id="f_about">{{ kb.about or "" }}</textarea></div>
<div class="card"><h2>Contact</h2>
  <label>Phone / WhatsApp</label><input type="text" id="f_phone" value="{{ (kb.contact or {}).phone or '' }}">
  <label>Email</label><input type="text" id="f_email" value="{{ (kb.contact or {}).email or '' }}">
  <label>Address / Campus</label><input type="text" id="f_address" value="{{ (kb.contact or {}).address or '' }}">
  <label>Office hours</label><input type="text" id="f_hours" value="{{ (kb.contact or {}).officeHours or '' }}">
</div>
{% endif %}

<div class="card"><h2>Courses</h2>
  <p class="muted">Prices in BDT. Leave the offer price blank to show "—". Extra columns apply per category.</p>
  <div id="categories"></div>
  <button type="button" class="ghost" onclick="addCategory()">+ Add category</button>
</div>

<div class="card"><h2>Books / Products</h2><textarea id="f_books">{{ kb.books or "" }}</textarea></div>
{% if role == "admin" %}
<div class="card"><h2>How to enroll / pay</h2><textarea id="f_enroll">{{ kb.enroll or "" }}</textarea></div>
{% endif %}

<div class="card"><h2>Custom sections</h2>
  <p class="muted">Free-form knowledge appended to the bot's knowledge base as written.
  {% if role == "admin" %}"Admin only" sections are hidden from the editor account.{% endif %}</p>
  <div id="sections"></div>
  <button type="button" class="ghost" onclick="addSection()">+ Add section</button>
</div>

<button onclick="serialize()">Save everything</button>
</form>

<script>
const IS_ADMIN = {{ "true" if role == "admin" else "false" }};
let kb = {{ kb_json | safe }};

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) node.appendChild(child);
  return node;
}

function priceInputs(course, key) {
  const p = course[key] || (course[key] = { regular: "", offer: "" });
  return el("td", {}, [
    el("input", { type: "text", placeholder: "offer", value: p.offer || "",
      oninput: e => p.offer = e.target.value }),
    el("input", { type: "text", placeholder: "regular", value: p.regular || "",
      oninput: e => p.regular = e.target.value }),
  ]);
}

function renderCategories() {
  const host = document.getElementById("categories");
  host.innerHTML = "";
  (kb.courseCategories ||= []).forEach((cat, ci) => {
    const extras = cat.extraColumns ||= [];
    const head = el("tr", {}, [
      el("th", { text: "Course" }), el("th", { text: "Online" }), el("th", { text: "Offline" }),
      el("th", { text: "Online+Offline" }), el("th", { text: "Duration" }), el("th", { text: "Starts" }),
      ...extras.map((name, xi) => el("th", {}, [
        el("input", { type: "text", value: name, oninput: e => extras[xi] = e.target.value }),
        el("button", { type: "button", class: "small ghost", text: "×",
          onclick: () => { extras.splice(xi, 1); cat.courses.forEach(c => (c.extra || []).splice(xi, 1)); renderCategories(); } }),
      ])),
      el("th", { text: "" }),
    ]);
    const rows = (cat.courses ||= []).map((course, ri) => el("tr", {}, [
      el("td", {}, [el("input", { type: "text", value: course.name || "", oninput: e => course.name = e.target.value })]),
      priceInputs(course, "online"), priceInputs(course, "offline"), priceInputs(course, "onlineOffline"),
      el("td", {}, [el("input", { type: "text", value: course.duration || "", oninput: e => course.duration = e.target.value })]),
      el("td", {}, [el("input", { type: "text", value: course.starts || "", oninput: e => course.starts = e.target.value })]),
      ...extras.map((_, xi) => el("td", {}, [el("input", { type: "text",
        value: (course.extra || [])[xi] || "",
        oninput: e => { (course.extra ||= [])[xi] = e.target.value; } })])),
      el("td", { class: "row-actions" }, [el("button", { type: "button", class: "small danger", text: "Delete",
        onclick: () => { cat.courses.splice(ri, 1); renderCategories(); } })]),
    ]));
    host.appendChild(el("div", { style: "margin-bottom:26px" }, [
      el("div", { style: "display:flex;gap:10px;align-items:center;margin-bottom:8px" }, [
        el("input", { type: "text", value: cat.name || "", style: "font-weight:600;max-width:420px",
          oninput: e => cat.name = e.target.value }),
        el("button", { type: "button", class: "small ghost", text: "+ column",
          onclick: () => { extras.push("New column"); renderCategories(); } }),
        el("button", { type: "button", class: "small ghost", text: "+ course",
          onclick: () => { cat.courses.push({ name: "", online: {}, offline: {}, onlineOffline: {}, duration: "", starts: "", extra: [] }); renderCategories(); } }),
        el("button", { type: "button", class: "small danger", text: "Delete category",
          onclick: () => { if (confirm("Delete this whole category?")) { kb.courseCategories.splice(ci, 1); renderCategories(); } } }),
      ]),
      el("div", { style: "overflow-x:auto" }, [el("table", {}, [head, ...rows])]),
    ]));
  });
}

function addCategory() {
  (kb.courseCategories ||= []).push({ name: "New category", extraColumns: [], courses: [] });
  renderCategories();
}

function renderSections() {
  const host = document.getElementById("sections");
  host.innerHTML = "";
  (kb.customSections ||= []).forEach((section, si) => {
    if (!IS_ADMIN && section.adminOnly) return; // hidden from editors; server keeps them
    host.appendChild(el("div", { style: "border:1px solid #e1e3ea;border-radius:8px;padding:12px;margin-bottom:12px" }, [
      el("input", { type: "text", value: section.title || "", placeholder: "Section title",
        style: "font-weight:600;margin-bottom:6px", oninput: e => section.title = e.target.value }),
      el("textarea", { oninput: e => section.body = e.target.value, text: section.body || "" }),
      el("div", { style: "display:flex;gap:12px;align-items:center;margin-top:6px" }, [
        ...(IS_ADMIN ? [el("label", { style: "margin:0;font-weight:400" }, [
          el("input", { type: "checkbox", ...(section.adminOnly ? { checked: "" } : {}),
            onchange: e => section.adminOnly = e.target.checked }),
          document.createTextNode(" Admin only (hidden from editor account)"),
        ])] : []),
        el("button", { type: "button", class: "small danger", text: "Delete section",
          onclick: () => { kb.customSections.splice(si, 1); renderSections(); } }),
      ]),
    ]));
  });
}

function addSection() {
  (kb.customSections ||= []).push({ title: "", body: "", adminOnly: false });
  renderSections();
}

function serialize() {
  if (IS_ADMIN) {
    kb.about = document.getElementById("f_about").value;
    kb.contact = {
      phone: document.getElementById("f_phone").value, email: document.getElementById("f_email").value,
      address: document.getElementById("f_address").value, officeHours: document.getElementById("f_hours").value,
    };
    kb.enroll = document.getElementById("f_enroll").value;
  }
  kb.books = document.getElementById("f_books").value;
  document.getElementById("kb_json").value = JSON.stringify(kb);
}

renderCategories();
renderSections();
</script>
{% endblock %}"""

PROMPT = """{% extends "base" %}{% block content %}
<form method="post" action="/system-prompt">
<input type="hidden" name="_csrf" value="{{ csrf }}">
<div class="card"><h2>System prompt (persona &amp; style)</h2>
<p class="muted">Blank reverts to the built-in default. The strict knowledge-base guardrails are always applied in code and cannot be edited here.</p>
<textarea name="system_prompt" style="min-height:260px">{{ system_prompt }}</textarea></div>

<div class="card"><h2>Answering rules</h2>
<textarea name="answering_rules" style="min-height:160px">{{ answering_rules }}</textarea></div>

<div class="card"><h2>Examples &amp; scenarios</h2>
<p class="muted">Appended to the prompt as guidance ("when X do Y", sample answers). One block per scenario.</p>
<div id="sections"></div>
<button type="button" class="ghost" onclick="addRow('','')">+ Add scenario</button>
<input type="hidden" name="sections_json" id="sections_json"></div>

<div class="card"><h2>Lead capture</h2>
<label>Instruction to the AI (when/how to ask for contact details)</label>
<textarea name="lead_instruction" style="min-height:140px">{{ lead_instruction }}</textarea>
<label>Proactively ask after this many customer turns</label>
<input type="number" name="ask_after_turns" min="1" value="{{ ask_after_turns }}" style="max-width:120px">
</div>
<button onclick="serialize()">Save bot behaviour</button>
</form>
<script>
let sections = {{ sections_json | safe }};
function esc(s){const d=document.createElement("div");d.textContent=s??"";return d.innerHTML;}
function render(){
  const host=document.getElementById("sections"); host.innerHTML="";
  sections.forEach((s,i)=>{
    const wrap=document.createElement("div");
    wrap.style.cssText="border:1px solid #e1e3ea;border-radius:8px;padding:12px;margin-bottom:12px";
    wrap.innerHTML=`<input type="text" value="${esc(s.title)}" placeholder="Title" style="font-weight:600;margin-bottom:6px">
      <textarea>${esc(s.body)}</textarea>
      <div style="margin-top:6px"><button type="button" class="small danger">Delete</button></div>`;
    wrap.querySelector("input").addEventListener("input",e=>s.title=e.target.value);
    wrap.querySelector("textarea").addEventListener("input",e=>s.body=e.target.value);
    wrap.querySelector("button").addEventListener("click",()=>{sections.splice(i,1);render();});
    host.appendChild(wrap);
  });
}
function addRow(t,b){sections.push({title:t,body:b});render();}
function serialize(){document.getElementById("sections_json").value=JSON.stringify(sections);}
render();
</script>
{% endblock %}"""

BLOCKED = """{% extends "base" %}{% block content %}
<div class="card"><h2>Block a sender</h2>
<form method="post" action="/blocked/add" style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
<input type="hidden" name="_csrf" value="{{ csrf }}">
<div><label>Sender ID (PSID)</label><input type="text" name="sender_id" style="min-width:220px"></div>
<div><label>Name (optional)</label><input type="text" name="name"></div>
<div><label>Note (optional)</label><input type="text" name="note"></div>
<div><button class="danger">Block</button></div>
</form></div>

<div class="card"><h2>Blocked users ({{ blocked | length }})</h2>
<input type="text" class="searchbox" placeholder="Search…" oninput="filterTable(this, 'blockedtable')">
<div class="scroll"><table id="blockedtable">
<tr><th>Name</th><th>Sender ID</th><th>Note</th><th>Blocked at</th><th></th></tr>
{% for b in blocked %}
<tr><td>{{ b.name or "—" }}</td><td>{{ b.senderId }}</td><td>{{ b.note or "" }}</td><td>{{ b.blocked_at[:16].replace("T", " ") }}</td>
<td><form method="post" action="/blocked/remove"><input type="hidden" name="_csrf" value="{{ csrf }}">
<input type="hidden" name="sender_id" value="{{ b.senderId }}"><button class="small ghost">Unblock</button></form></td></tr>
{% endfor %}
</table></div></div>

<div class="card"><h2>Recent senders (last 7 days, busiest first)</h2>
<p class="muted">Find a nuisance sender by name or last message and block them in one click.</p>
<input type="text" class="searchbox" placeholder="Search…" oninput="filterTable(this, 'recenttable')">
<div class="scroll"><table id="recenttable">
<tr><th>Name</th><th>Last message</th><th>Msgs</th><th>Last seen</th><th></th></tr>
{% for r in recents %}
<tr><td>{{ r.name or "—" }}</td><td>{{ r.last_message }}</td><td>{{ r.msg_count }}</td><td>{{ r.last_seen_str }}</td>
<td><form method="post" action="/blocked/add"><input type="hidden" name="_csrf" value="{{ csrf }}">
<input type="hidden" name="sender_id" value="{{ r.sender_id }}"><input type="hidden" name="name" value="{{ r.name }}">
<button class="small danger">Block</button></form></td></tr>
{% endfor %}
</table></div></div>
<script>
function filterTable(box, id){
  const q = box.value.toLowerCase();
  for (const row of document.querySelectorAll(`#${id} tr:not(:first-child)`))
    row.style.display = row.textContent.toLowerCase().includes(q) ? "" : "none";
}
</script>
{% endblock %}"""

LEADS = """{% extends "base" %}{% block content %}
<div class="card"><h2>Captured leads ({{ leads | length }} most recent)</h2>
<input type="text" class="searchbox" placeholder="Search…" oninput="filterTable(this, 'leadstable')">
<div class="scroll"><table id="leadstable">
<tr><th>Time</th><th>Name</th><th>Phone</th><th>Interested in</th><th>Remarks</th><th>Chat</th></tr>
{% for l in leads %}
<tr><td>{{ l.time }}</td><td>{{ l.name }}</td><td>{{ l.phone }}</td><td>{{ l.interest }}</td><td>{{ l.remarks }}</td>
<td>{% if l.url %}<a href="{{ l.url }}" target="_blank">Open</a>{% endif %}</td></tr>
{% endfor %}
</table></div>
<p><a href="/leads.csv">Download CSV</a></p></div>
<script>
function filterTable(box, id){
  const q = box.value.toLowerCase();
  for (const row of document.querySelectorAll(`#${id} tr:not(:first-child)`))
    row.style.display = row.textContent.toLowerCase().includes(q) ? "" : "none";
}
</script>
{% endblock %}"""

env = Environment(
    loader=DictLoader({"base": BASE, "login": LOGIN, "kb": KB, "prompt": PROMPT, "blocked": BLOCKED, "leads": LEADS}),
    autoescape=select_autoescape(default=True),
)
