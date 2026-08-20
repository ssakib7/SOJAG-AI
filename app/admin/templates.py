"""Jinja2 templates for the admin panel — a faithful port of the Node bot's admin.js.

Same markup, same class names, same client-side behaviour (add course / add column /
add section / add scenario, table search, sticky save bar, animated login), so the panel
looks and works exactly like the one the team already uses. CSS lives in styles.py,
copied verbatim; the bracket-notation form fields are parsed by forms.py.
"""

from jinja2 import DictLoader, Environment, select_autoescape

from app.admin.styles import LOGIN_STYLE, PAGE_STYLE

FONTS = (
    '<link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@300;400;500;600;700'
    '&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">'
)

# --- Login -------------------------------------------------------------------
LOGIN = """<!doctype html><html lang="bn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>De Jure Academy — Admin Login</title>
""" + FONTS + """
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>{{ login_style | safe }}</style></head>
<body>
<canvas id="cvs"></canvas>
<div class="page">
  <div class="card">
    <div class="badge-ai"><span class="dot-pulse"></span> LIVE AI</div>

    <div class="logo-wrap">
      <div class="logo-box"><span class="logo-brain">🧠</span></div>
      <div class="logo-name">SOJAG <span class="dot">✦</span> <span class="ai">AI</span></div>
      <div class="logo-badge">De Jure Academy</div>
    </div>

    <div class="welcome">
      <h1>আপনাকে স্বাগতম! ✨</h1>
      <p>বাংলাদেশের সবচেয়ে শক্তিশালী<br/>
        <strong>AI এজেন্ট</strong>-এ আপনার অ্যাকাউন্টে লগইন করুন</p>
    </div>

    {% if error %}<div class="server-err"><i class="fa-solid fa-circle-exclamation"></i><span>{{ error }}</span></div>{% endif %}

    <form id="loginForm" method="post" action="/login" novalidate>
      <div class="field" id="f-user">
        <label for="username">ইউজারনেম / ইমেইল</label>
        <div class="inp-wrap">
          <i class="fa-solid fa-envelope inp-icon"></i>
          <input id="username" name="username" type="text" class="inp"
            placeholder="আপনার ইউজারনেম বা ইমেইল লিখুন" autocomplete="username" autofocus>
        </div>
        <div class="err-msg">⚠️ এই ঘরটি পূরণ করা আবশ্যক</div>
      </div>

      <div class="field" id="f-pass">
        <label for="password">পাসওয়ার্ড</label>
        <div class="inp-wrap">
          <i class="fa-solid fa-lock inp-icon"></i>
          <input id="password" name="password" type="password" class="inp pr"
            placeholder="আপনার পাসওয়ার্ড লিখুন" autocomplete="current-password">
          <button type="button" class="pw-toggle" id="pwToggle" aria-label="পাসওয়ার্ড দেখুন">
            <i class="fa-solid fa-eye" id="eyeIcon"></i>
          </button>
        </div>
        <div class="err-msg">⚠️ পাসওয়ার্ড দিতে হবে</div>
      </div>

      <div class="row-opts">
        <label class="chk-label"><input type="checkbox" name="remember"> আমাকে মনে রাখুন</label>
        <a href="#" class="forgot">পাসওয়ার্ড ভুলে গেছেন?</a>
      </div>

      <button type="submit" class="btn-login" id="submitBtn">লগইন করুন &nbsp;→</button>
    </form>

    <div class="divider"><span>অথবা</span></div>
    <div class="card-foot">অ্যাকাউন্ট নেই? &nbsp;<a href="#">অ্যাডমিনের সাথে যোগাযোগ করুন</a></div>
  </div>

  <div class="bottom-bar">© 2025 SOJAG AI &nbsp;·&nbsp; Powered by De Jure Academy &nbsp;·&nbsp; Bangladesh</div>
</div>

<script>
{% raw %}
/* Circuit board background */
(function () {
  var cvs = document.getElementById('cvs');
  var ctx = cvs.getContext('2d');
  var W, H, nodes = [], lines = [];
  var ORANGE = '#F5821E', CYAN = '#00CAFF';

  function buildGraph() {
    nodes = []; lines = [];
    var cols = Math.ceil(W / 110) + 1, rows = Math.ceil(H / 110) + 1;
    for (var c = 0; c < cols; c++) {
      for (var r = 0; r < rows; r++) {
        if (Math.random() < .55) {
          nodes.push({
            x: c * 110 + (Math.random() - .5) * 55,
            y: r * 110 + (Math.random() - .5) * 55,
            r: Math.random() * 1.8 + 1,
            ph: Math.random() * Math.PI * 2,
            color: c < cols / 2 ? ORANGE : CYAN,
            speed: .018 + Math.random() * .012,
          });
        }
      }
    }
    nodes.forEach(function (a, i) {
      nodes.slice(i + 1).forEach(function (b) {
        var dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy);
        if (d < 165 && Math.random() < .45) {
          var ex = Math.random() < .5 ? a.x : b.x;
          var ey = Math.random() < .5 ? a.y : b.y;
          lines.push({ a: a, b: b, ex: ex, ey: ey, color: a.color, alpha: (1 - d / 165) * .18 });
        }
      });
    });
  }

  function resize() {
    W = cvs.width = window.innerWidth;
    H = cvs.height = window.innerHeight;
    buildGraph();
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    var bg = ctx.createRadialGradient(W*.5, H*.4, 0, W*.5, H*.5, Math.max(W,H)*.8);
    bg.addColorStop(0, '#0E1F38'); bg.addColorStop(1, '#060C18');
    ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
    lines.forEach(function (l) {
      ctx.beginPath();
      ctx.moveTo(l.a.x, l.a.y);
      ctx.lineTo(l.ex, l.ey === l.a.y ? l.a.y : l.ey);
      ctx.lineTo(l.b.x, l.b.y);
      ctx.strokeStyle = l.color === ORANGE ? 'rgba(245,130,30,' + l.alpha + ')' : 'rgba(0,202,255,' + l.alpha + ')';
      ctx.lineWidth = 1; ctx.stroke();
    });
    nodes.forEach(function (n) {
      n.ph += n.speed;
      var glow = .45 + Math.sin(n.ph) * .2;
      var g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, 9);
      var isO = n.color === ORANGE;
      g.addColorStop(0, isO ? 'rgba(245,130,30,' + (glow*.6) + ')' : 'rgba(0,202,255,' + (glow*.6) + ')');
      g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g; ctx.fillRect(n.x - 9, n.y - 9, 18, 18);
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r + Math.sin(n.ph) * .4, 0, Math.PI * 2);
      ctx.fillStyle = n.color; ctx.globalAlpha = glow + .15; ctx.fill(); ctx.globalAlpha = 1;
    });
    requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize);
  resize();
  draw();
})();

/* Password toggle */
document.getElementById('pwToggle').addEventListener('click', function () {
  var inp = document.getElementById('password'), icon = document.getElementById('eyeIcon');
  if (inp.type === 'password') { inp.type = 'text'; icon.classList.replace('fa-eye', 'fa-eye-slash'); }
  else { inp.type = 'password'; icon.classList.replace('fa-eye-slash', 'fa-eye'); }
});

/* Client-side validation, then let the form submit normally to the server */
document.getElementById('loginForm').addEventListener('submit', function (e) {
  var userVal = document.getElementById('username').value.trim();
  var passVal = document.getElementById('password').value.trim();
  var fUser = document.getElementById('f-user'), fPass = document.getElementById('f-pass');
  fUser.classList.remove('has-error'); fPass.classList.remove('has-error');
  var ok = true;
  if (!userVal) { fUser.classList.add('has-error'); ok = false; }
  if (!passVal) { fPass.classList.add('has-error'); ok = false; }
  if (!ok) { e.preventDefault(); return; }
  var btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = '⏳  লগইন হচ্ছে...';
});
{% endraw %}
</script>
</body></html>"""

# --- Shared shell + sidebar ---------------------------------------------------
SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} — De Jure Academy</title>
""" + FONTS + """
<style>{{ page_style | safe }}</style>{% block head %}{% endblock %}</head>
<body>
<div class="layout">
{% if user %}{% include "sidebar" %}{% endif %}
<main class="main">
{% block content %}{% endblock %}
</main>
</div>
{% block scripts %}{% endblock %}
</body></html>"""

SIDEBAR = """<aside class="sidebar">
  <div class="brand">
    <div class="mark">🧠</div>
    <div>
      <div class="brand-name">SOJAG <span class="ai">AI</span></div>
      <div class="brand-sub">De Jure Academy</div>
    </div>
  </div>
  <nav class="nav">
    {% if role == "admin" %}
    <a href="/system-prompt"{% if active == "prompt" %} class="nav-active"{% endif %}>Bot behaviour</a>
    {%- if active == "prompt" %}
    <a class="nav-sub" href="#sec-prompt">System prompt</a>
    <a class="nav-sub" href="#sec-rules">Answering rules</a>
    <a class="nav-sub" href="#sec-scenarios">Examples &amp; scenarios</a>
    <a class="nav-sub" href="#sec-lead">Lead capture</a>
    {%- endif %}
    <a href="/leads"{% if active == "leads" %} class="nav-active"{% endif %}>Leads</a>
    {% endif %}
    <a href="/blocked"{% if active == "blocked" %} class="nav-active"{% endif %}>Blocked users</a>
    <div class="nav-label">Knowledge base</div>
    {% set base = "#" if active == "kb" else "/#" %}
    {% if role == "admin" %}
    <a href="{{ base }}sec-about">About</a>
    <a href="{{ base }}sec-contact">Contact</a>
    {% endif %}
    <a href="{{ base }}sec-courses">Courses</a>
    <a href="{{ base }}sec-books">Books &amp; products</a>
    {% if role == "admin" %}<a href="{{ base }}sec-enroll">Enrollment</a>{% endif %}
    <a href="{{ base }}sec-custom">Custom sections</a>
  </nav>
  <div class="nav-foot">
    {% if user %}<div class="who">{{ user }} · {% if role == "admin" %}Admin{% else %}Courses &amp; products{% endif %}</div>{% endif %}
    <form method="post" action="/logout">
      <input type="hidden" name="_csrf" value="{{ csrf }}">
      <button type="submit" class="btn-ghost">Log out</button>
    </form>
  </div>
</aside>"""

FORBIDDEN = """{% extends "shell" %}{% block content %}
<h1 class="page-title">Not allowed</h1>
<p class="lede">{{ user or "This account" }} can edit <strong>courses, books/products and custom sections</strong> and manage <strong>blocked users</strong> only.
Bot behaviour and the rest of the knowledge base can be changed by an administrator.</p>
<p><a class="btn-ghost" href="/">← Back to courses &amp; products</a></p>
{% endblock %}"""

# --- Knowledge base editor ----------------------------------------------------
KB = """{% extends "shell" %}{% block content %}
<h1 class="page-title">{% if role == "admin" %}Knowledge Base{% else %}Courses &amp; products{% endif %}</h1>
<p class="lede">{% if role == "admin" %}The facts the bot answers from. Its core instructions live separately under <a href="/system-prompt">System prompt</a>. Changes take effect for new replies as soon as you save — no restart needed.{% else %}Course listings, books/products and custom sections (FAQs, promotions, policies…) the bot answers from. Changes take effect for new replies as soon as you save — no restart needed. Other knowledge-base sections and the bot's behaviour are managed by an administrator.{% endif %}</p>
{% if saved %}<div class="flash">Saved. The bot is now using the updated knowledge base.</div>{% endif %}

<form method="post" action="/save">
<input type="hidden" name="_csrf" value="{{ csrf }}">
{% if role == "admin" %}
<section class="card" id="sec-about">
  <h2>About</h2>
  <p class="hint">Supports markdown. Include core values, mission, and vision here as <code>###</code> subsections.</p>
  <label for="about">About De Jure Academy</label>
  <textarea id="about" name="about">{{ kb.about or "" }}</textarea>
</section>

<section class="card" id="sec-contact">
  <h2>Contact</h2>
  <p class="hint">Leave a field blank and the bot will treat it as not-yet-available.</p>
  <div class="grid">
    <div><label for="contact-phone">Phone / WhatsApp</label>
    <input type="text" id="contact-phone" name="contact[phone]" value="{{ contact.phone or '' }}" placeholder="+8801XXXXXXXXX"></div>
    <div><label for="contact-email">Email</label>
    <input type="text" id="contact-email" name="contact[email]" value="{{ contact.email or '' }}" placeholder="info@dejureacademy.com"></div>
    <div><label for="contact-address">Address / Campus</label>
    <input type="text" id="contact-address" name="contact[address]" value="{{ contact.address or '' }}" placeholder="Dhaka"></div>
    <div><label for="contact-hours">Office hours</label>
    <input type="text" id="contact-hours" name="contact[officeHours]" value="{{ contact.officeHours or '' }}" placeholder="Sat–Thu, 10am–6pm"></div>
  </div>
</section>
{% endif %}

<div id="sec-courses">
{% for cat in categories %}
<section class="card">
  <h2>{{ cat.name }}</h2>
  <p class="hint">Prices are in BDT. Enter the offer price first, then the regular price (leave blank if there's no discount or the mode isn't offered). Use “+ Add column” for any extra detail (e.g. class days, instructor).</p>
  <input type="hidden" name="categories[{{ loop.index0 }}][name]" value="{{ cat.name }}">
  {% set ci = loop.index0 %}
  {% set cols = cat.extraColumns or [] %}
  <div class="table-wrap"><table><thead><tr>
    <th>Course</th><th>Online · offer / reg</th><th>Offline</th><th>Online+Offline</th>
    <th>Duration</th><th>Starts</th>
    {%- for col in cols %}
    <th class="extra-col" data-col="{{ loop.index0 }}"><div class="col-head">
      <input type="text" name="categories[{{ ci }}][extraColumns][{{ loop.index0 }}]" value="{{ col }}" placeholder="Column name">
      <button type="button" class="col-del" title="Remove column" onclick="removeColumn({{ ci }}, {{ loop.index0 }})">✕</button>
    </div></th>
    {%- endfor %}
    <th></th>
  </tr></thead>
  <tbody data-cat="{{ ci }}" data-next="{{ (cat.courses or []) | length }}" data-nextcol="{{ cols | length }}">
  {% for course in cat.courses or [] %}
  {% set ri = loop.index0 %}
  {% set path = "categories[" ~ ci ~ "][courses][" ~ ri ~ "]" %}
  <tr data-row="{{ ri }}">
    <td class="col-name"><input type="text" name="{{ path }}[name]" value="{{ course.name or '' }}" placeholder="Course name"></td>
    {%- for mode in ["online", "offline", "onlineOffline"] %}
    <td class="col-num"><div class="price-pair">
      <input type="text" name="{{ path }}[{{ mode }}][offer]" value="{{ (course[mode] or {}).offer or '' }}" placeholder="offer">
      <span class="sep">/</span>
      <input type="text" name="{{ path }}[{{ mode }}][regular]" value="{{ (course[mode] or {}).regular or '' }}" placeholder="reg">
    </div></td>
    {%- endfor %}
    <td><input type="text" name="{{ path }}[duration]" value="{{ course.duration or '' }}" placeholder="e.g. 6 mo"></td>
    <td><input type="text" name="{{ path }}[starts]" value="{{ course.starts or '' }}" placeholder="YYYY-MM-DD"></td>
    {%- for col in cols %}
    <td data-col="{{ loop.index0 }}"><input type="text" name="{{ path }}[extra][{{ loop.index0 }}]" value="{{ (course.extra or [])[loop.index0] if (course.extra or []) | length > loop.index0 else '' }}" placeholder="—"></td>
    {%- endfor %}
    <td><button type="button" class="icon-btn" title="Remove course" onclick="this.closest('tr').remove()">✕</button></td>
  </tr>
  {% endfor %}
  </tbody></table></div>
  <div class="table-actions">
    <button type="button" class="add-row" onclick="addRow({{ ci }})">+ Add course</button>
    <button type="button" class="add-row" onclick="addColumn({{ ci }})">+ Add column</button>
  </div>
</section>
{% endfor %}
</div>

<section class="card" id="sec-books">
  <h2>Books &amp; products</h2>
  <label for="books">Books</label>
  <textarea id="books" name="books">{{ kb.books or "" }}</textarea>
  <p class="hint" style="margin-top:6px">Markdown — one product per line.</p>
</section>
{% if role == "admin" %}
<section class="card" id="sec-enroll">
  <h2>How to enroll / pay</h2>
  <label for="enroll">Enrollment instructions</label>
  <textarea id="enroll" name="enroll">{{ kb.enroll or "" }}</textarea>
  <p class="hint" style="margin-top:6px">Markdown.</p>
</section>
{% endif %}

<div id="sec-custom">
  <div class="custom-list" data-next="{{ custom_sections | length }}">
  {% for section in custom_sections %}
  {% set path = "customSections[" ~ loop.index0 ~ "]" %}
  <section class="card custom-section" data-section="{{ loop.index0 }}">
    <div class="custom-head">
      <input type="text" class="custom-title" name="{{ path }}[title]" value="{{ section.title or '' }}"
        placeholder="Section title (e.g. Lead messages, FAQ, Promotions)">
      <button type="button" class="icon-btn" title="Remove section" onclick="this.closest('.custom-section').remove()">✕</button>
    </div>
    <p class="hint">Markdown. The bot uses this as part of its knowledge base, exactly as written.</p>
    <textarea name="{{ path }}[body]" placeholder="Write this section's content here…">{{ section.body or "" }}</textarea>
    {% if role == "admin" %}
    <label class="hint" style="display:flex;align-items:center;gap:8px;margin-top:10px;cursor:pointer">
      <input type="checkbox" name="{{ path }}[adminOnly]" value="1"{% if section.adminOnly %} checked{% endif %}>
      Admin only — hide this section from the courses &amp; products account (the bot still uses it)
    </label>
    {% endif %}
  </section>
  {% endfor %}
  </div>
  <p class="custom-empty"{% if custom_sections %} style="display:none"{% endif %}>
    No custom sections yet. Add one for anything not covered above — lead messages, FAQs, promotions, policies — and the bot will use it.
  </p>
  <button type="button" class="add-row" onclick="addSection()">+ Add section</button>
</div>

<div class="savebar"><div class="savebar-inner">
  <span class="note">Changes apply instantly after saving.</span>
  <span class="spacer"></span>
  <button type="submit" class="btn-primary">Save changes</button>
</div></div>
</form>
{% endblock %}

{% block scripts %}
<script>
var IS_ADMIN = {% if role == "admin" %}true{% else %}false{% endif %};
{% raw %}
function priceCell(path, mode) {
  return '<td class="col-num"><div class="price-pair">' +
    '<input type="text" name="' + path + '[' + mode + '][offer]" placeholder="offer">' +
    '<span class="sep">/</span>' +
    '<input type="text" name="' + path + '[' + mode + '][regular]" placeholder="reg">' +
    '</div></td>';
}
function tableFor(ci) {
  return document.querySelector('tbody[data-cat="' + ci + '"]').closest('table');
}
function currentCols(ci) {
  var cols = [];
  tableFor(ci).querySelectorAll('thead th.extra-col').forEach(function (th) {
    cols.push(th.getAttribute('data-col'));
  });
  return cols;
}
function addRow(ci) {
  var tbody = document.querySelector('tbody[data-cat="' + ci + '"]');
  var ri = parseInt(tbody.getAttribute('data-next'), 10);
  tbody.setAttribute('data-next', ri + 1);
  var path = 'categories[' + ci + '][courses][' + ri + ']';
  var extra = currentCols(ci).map(function (k) {
    return '<td data-col="' + k + '"><input type="text" name="' + path + '[extra][' + k + ']" placeholder="—"></td>';
  }).join('');
  var tr = document.createElement('tr');
  tr.setAttribute('data-row', ri);
  tr.innerHTML =
    '<td class="col-name"><input type="text" name="' + path + '[name]" placeholder="Course name"></td>' +
    priceCell(path, 'online') + priceCell(path, 'offline') + priceCell(path, 'onlineOffline') +
    '<td><input type="text" name="' + path + '[duration]" placeholder="e.g. 6 mo"></td>' +
    '<td><input type="text" name="' + path + '[starts]" placeholder="YYYY-MM-DD"></td>' +
    extra +
    '<td><button type="button" class="icon-btn" title="Remove course" onclick="this.closest(\\'tr\\').remove()">✕</button></td>';
  tbody.appendChild(tr);
  tr.querySelector('input').focus();
}
function addColumn(ci) {
  var table = tableFor(ci);
  var tbody = table.querySelector('tbody');
  var k = parseInt(tbody.getAttribute('data-nextcol'), 10);
  tbody.setAttribute('data-nextcol', k + 1);
  var headRow = table.querySelector('thead tr');
  var th = document.createElement('th');
  th.className = 'extra-col';
  th.setAttribute('data-col', k);
  th.innerHTML = '<div class="col-head">' +
    '<input type="text" name="categories[' + ci + '][extraColumns][' + k + ']" placeholder="Column name">' +
    '<button type="button" class="col-del" title="Remove column" onclick="removeColumn(' + ci + ',' + k + ')">✕</button>' +
    '</div>';
  headRow.insertBefore(th, headRow.lastElementChild);
  tbody.querySelectorAll('tr').forEach(function (tr) {
    var ri = tr.getAttribute('data-row');
    var td = document.createElement('td');
    td.setAttribute('data-col', k);
    td.innerHTML = '<input type="text" name="categories[' + ci + '][courses][' + ri + '][extra][' + k + ']" placeholder="—">';
    tr.insertBefore(td, tr.lastElementChild);
  });
  th.querySelector('input').focus();
}
function removeColumn(ci, k) {
  var table = tableFor(ci);
  var th = table.querySelector('thead th[data-col="' + k + '"]');
  if (th) th.remove();
  table.querySelectorAll('tbody td[data-col="' + k + '"]').forEach(function (td) { td.remove(); });
}
function addSection() {
  var list = document.querySelector('#sec-custom .custom-list');
  var i = parseInt(list.getAttribute('data-next'), 10);
  list.setAttribute('data-next', i + 1);
  var path = 'customSections[' + i + ']';
  var sec = document.createElement('section');
  sec.className = 'card custom-section';
  sec.setAttribute('data-section', i);
  sec.innerHTML =
    '<div class="custom-head">' +
      '<input type="text" class="custom-title" name="' + path + '[title]" placeholder="Section title (e.g. Lead messages, FAQ, Promotions)">' +
      '<button type="button" class="icon-btn" title="Remove section" onclick="this.closest(\\'.custom-section\\').remove()">✕</button>' +
    '</div>' +
    '<p class="hint">Markdown. The bot uses this as part of its knowledge base, exactly as written.</p>' +
    '<textarea name="' + path + '[body]" placeholder="Write this section\\'s content here…"></textarea>' +
    (IS_ADMIN
      ? '<label class="hint" style="display:flex;align-items:center;gap:8px;margin-top:10px;cursor:pointer">' +
        '<input type="checkbox" name="' + path + '[adminOnly]" value="1"> ' +
        'Admin only — hide this section from the courses &amp; products account (the bot still uses it)</label>'
      : '');
  list.appendChild(sec);
  var empty = document.querySelector('#sec-custom .custom-empty');
  if (empty) empty.style.display = 'none';
  sec.querySelector('input').focus();
}
{% endraw %}
</script>
{% endblock %}"""

# --- Bot behaviour ------------------------------------------------------------
PROMPT = """{% extends "shell" %}{% block content %}
<h1 class="page-title">Bot behaviour</h1>
<p class="lede">How the bot replies: its <strong>system prompt</strong> (core instructions for the AI) and <strong>examples &amp; scenarios</strong> (guidance for specific situations — like collecting a lead). The AI writes every reply from these; the <a href="/">knowledge base</a> — the facts — lives separately. Changes take effect for new replies as soon as you save — no restart needed.</p>
{% if saved %}<div class="flash">Saved. The bot is now using the updated settings.</div>{% endif %}

<form method="post" action="/system-prompt">
<input type="hidden" name="_csrf" value="{{ csrf }}">

<section class="card" id="sec-prompt">
  <h2>System prompt</h2>
  <p class="hint">The core instructions sent to the AI on every message. The knowledge base is appended automatically, so don't paste facts here. Leave blank to restore the built-in default (shown as the placeholder).</p>
  <textarea id="systemPrompt" name="systemPrompt" class="prompt-input" placeholder="{{ default_prompt }}">{{ system_prompt }}</textarea>
</section>

<section class="card" id="sec-rules">
  <h2>Answering rules</h2>
  <p class="hint">Do's and don'ts the bot always follows (e.g. never invent prices, always reply in Bengali). Added to the prompt as instructions. Markdown. Leave blank to restore the default (shown as placeholder).</p>
  <textarea id="answeringRules" name="answeringRules" placeholder="{{ default_rules }}">{{ answering_rules }}</textarea>
</section>

<div id="sec-scenarios">
  <h2 class="group-head">Examples &amp; scenarios</h2>
  <p class="group-sub">Free-form guidance appended to the system prompt. Use it for an example lead conversation, or a “when a customer does X, respond with Y” rule — the bot follows these instead of relying on fixed canned replies.</p>
  <div class="scenario-list" data-next="{{ sections | length }}">
  {% for s in sections %}
  {% set path = "promptSections[" ~ loop.index0 ~ "]" %}
  <section class="card scenario-section" data-section="{{ loop.index0 }}">
    <div class="custom-head">
      <input type="text" class="custom-title" name="{{ path }}[title]" value="{{ s.title or '' }}"
        placeholder="Scenario title (e.g. Example lead, Refund question)">
      <button type="button" class="icon-btn" title="Remove scenario" onclick="this.closest('.scenario-section').remove()">✕</button>
    </div>
    <p class="hint">Markdown, added to the system prompt. Great for an example conversation or a “when a customer does X, respond with Y” rule.</p>
    <textarea name="{{ path }}[body]" placeholder="e.g. When a customer wants to enroll, warmly ask for their name and phone number, then…">{{ s.body or "" }}</textarea>
  </section>
  {% endfor %}
  </div>
  <p class="custom-empty"{% if sections %} style="display:none"{% endif %}>No scenarios yet. Add an example lead, a refund-question playbook, or any “when X, do Y” rule — the bot will follow it.</p>
  <button type="button" class="add-row" onclick="addScenario()">+ Add scenario</button>
</div>

<section class="card" id="sec-lead">
  <h2>Lead capture</h2>
  <p class="hint">How and when the bot collects a customer's contact details. It asks them to send their name + phone, then a <code>save_lead</code> tool saves it to your Sheet/Telegram (the phone is validated in code). The bot also writes a short note on what the customer is interested in and how to handle them, and sends it along with the lead. Only used when lead capture is configured. Leave the instruction blank to restore the default (shown as placeholder).</p>
  <label for="leadInstruction">Lead-capture instruction (added to the prompt)</label>
  <textarea id="leadInstruction" name="leadInstruction" placeholder="{{ default_lead }}">{{ lead_instruction }}</textarea>
  <label for="leadAskAfterTurns">Proactively ask after this many messages</label>
  <input type="number" id="leadAskAfterTurns" name="leadAskAfterTurns" min="1" max="20" value="{{ ask_after_turns }}" style="max-width:130px">
  <p class="hint" style="margin-top:6px">The bot also asks sooner if it senses clear intent — this is the backstop. Default {{ default_turns }}.</p>
</section>

<div class="savebar"><div class="savebar-inner">
  <span class="note">Changes apply instantly after saving.</span>
  <span class="spacer"></span>
  <button type="submit" class="btn-primary">Save changes</button>
</div></div>
</form>
{% endblock %}

{% block scripts %}
<script>
{% raw %}
function addScenario() {
  var list = document.querySelector('#sec-scenarios .scenario-list');
  var i = parseInt(list.getAttribute('data-next'), 10);
  list.setAttribute('data-next', i + 1);
  var path = 'promptSections[' + i + ']';
  var sec = document.createElement('section');
  sec.className = 'card scenario-section';
  sec.setAttribute('data-section', i);
  sec.innerHTML =
    '<div class="custom-head">' +
      '<input type="text" class="custom-title" name="' + path + '[title]" placeholder="Scenario title (e.g. Example lead, Refund question)">' +
      '<button type="button" class="icon-btn" title="Remove scenario" onclick="this.closest(\\'.scenario-section\\').remove()">✕</button>' +
    '</div>' +
    '<p class="hint">Markdown, added to the system prompt so the AI follows it.</p>' +
    '<textarea name="' + path + '[body]" placeholder="e.g. When a customer wants to enroll, ask for their name and phone…"></textarea>';
  list.appendChild(sec);
  var empty = document.querySelector('#sec-scenarios .custom-empty');
  if (empty) empty.style.display = 'none';
  sec.querySelector('input').focus();
}
{% endraw %}
</script>
{% endblock %}"""

# --- Blocked users ------------------------------------------------------------
BLOCKED = """{% extends "shell" %}
{% block head %}<style>
  /* Long rosters scroll inside their card instead of stretching the page. The header row
     stays pinned; it needs an opaque background or scrolled rows show through it. */
  .scroll-table { max-height: 430px; overflow-y: auto; }
  .scroll-table thead th { position: sticky; top: 0; z-index: 1; background: #102035; }
</style>{% endblock %}
{% block content %}
<h1 class="page-title">Blocked users</h1>
<p class="lede">Senders the bot <strong>never replies to</strong>. Their messages are dropped before any AI call, so they cost nothing. Blocking is silent and reversible: the person can still message the page and your team can still read them in the Meta inbox — the bot just stays quiet. Takes effect immediately.</p>
{% if done == "blocked" %}<div class="flash">Blocked. The bot will not reply to this sender from now on.</div>
{% elif done == "unblocked" %}<div class="flash">Unblocked. The bot will answer this sender again.</div>
{% elif done == "invalid" %}<div class="flash" style="color:var(--red);border-color:rgba(255,107,107,.3);background:rgba(255,107,107,.08)">No sender ID given — nothing was blocked.</div>{% endif %}

<section class="card">
  <h2>Block a sender</h2>
  <p class="hint">The sender ID (PSID) is the long number shown next to every message in the server logs, e.g. <code>← [31728367163692xxx] …</code> — or just click Block next to a recent conversation below.</p>
  <form method="post" action="/blocked/add">
    <input type="hidden" name="_csrf" value="{{ csrf }}">
    <div class="grid">
      <div><label for="senderId">Sender ID (PSID)</label>
      <input type="text" id="senderId" name="senderId" required placeholder="e.g. 31728367163692xxx" inputmode="numeric"></div>
      <div><label for="note">Note (optional — why blocked)</label>
      <input type="text" id="note" name="note" placeholder="e.g. spamming stickers all day"></div>
    </div>
    <button type="submit" class="btn-primary" style="margin-top:16px">Block sender</button>
  </form>
</section>

<section class="card">
  <h2>Currently blocked{% if blocked %} ({{ blocked | length }}){% endif %}</h2>
  {% if blocked %}
  <input type="search" placeholder="Search by name, ID or note…" oninput="filterTable(this, 'blocked-table')" style="margin:14px 0">
  <div class="table-wrap scroll-table"><table id="blocked-table" style="min-width:640px">
  <thead><tr><th>Sender ID</th><th>Name</th><th>Note</th><th>Blocked</th><th></th></tr></thead>
  <tbody>
  {% for b in blocked %}
  <tr>
    <td><code>{{ b.senderId }}</code></td>
    <td>{% if b.name %}{{ b.name }}{% else %}<span style="color:var(--muted-2)">—</span>{% endif %}</td>
    <td>{% if b.note %}{{ b.note }}{% else %}<span style="color:var(--muted-2)">—</span>{% endif %}</td>
    <td style="white-space:nowrap">{{ b.when }}</td>
    <td><form method="post" action="/blocked/remove" style="margin:0"><input type="hidden" name="_csrf" value="{{ csrf }}">
      <input type="hidden" name="senderId" value="{{ b.senderId }}">
      <button type="submit" class="btn-ghost" style="padding:5px 12px;font-size:13px">Unblock</button>
    </form></td>
  </tr>
  {% endfor %}
  </tbody></table></div>
  {% else %}<p class="hint">Nobody is blocked. The bot replies to everyone who messages the page.</p>{% endif %}
</section>

<section class="card">
  <h2>Recent senders — last 7 days</h2>
  <p class="hint">Everyone who messaged the bot in the past week, <strong>busiest first</strong> — the ones wasting the most tokens sit at the top. Names appear when Facebook's profile lookup resolves them (or when the customer typed their name in chat). The list fills as messages arrive, so senders from before this feature went live appear once they message again.</p>
  {% if recents %}
  <input type="search" placeholder="Search by name, ID or message…" oninput="filterTable(this, 'recent-table')" style="margin:14px 0">
  <div class="table-wrap scroll-table"><table id="recent-table" style="min-width:720px">
  <thead><tr><th>Name</th><th>Sender ID</th><th>Last message</th><th>Msgs</th><th>Last active</th><th></th></tr></thead>
  <tbody>
  {% for r in recents %}
  <tr>
    <td>{% if r.name %}{{ r.name }}{% else %}<span style="color:var(--muted-2)">—</span>{% endif %}</td>
    <td><code>{{ r.sender_id }}</code></td>
    <td>{% if r.last_message %}{{ r.last_message }}{% else %}<span style="color:var(--muted-2)">—</span>{% endif %}</td>
    <td class="col-num" style="text-align:right">{{ r.msg_count }}</td>
    <td style="white-space:nowrap">{{ r.when }}</td>
    <td><form method="post" action="/blocked/add" style="margin:0"><input type="hidden" name="_csrf" value="{{ csrf }}">
      <input type="hidden" name="senderId" value="{{ r.sender_id }}">
      <input type="hidden" name="name" value="{{ r.name }}">
      <button type="submit" class="btn-ghost" style="padding:5px 12px;font-size:13px">Block</button>
    </form></td>
  </tr>
  {% endfor %}
  </tbody></table></div>
  <p class="hint" id="recent-empty" style="display:none;margin-top:12px">No sender matches that search.</p>
  {% else %}<p class="hint" style="margin-top:12px">Nobody yet — the list fills as messages arrive.</p>{% endif %}
</section>
{% endblock %}

{% block scripts %}
<script>
{% raw %}
function filterTable(input, tableId) {
  var q = input.value.trim().toLowerCase();
  var rows = document.querySelectorAll('#' + tableId + ' tbody tr');
  var shown = 0;
  for (var i = 0; i < rows.length; i++) {
    var hit = rows[i].textContent.toLowerCase().indexOf(q) !== -1;
    rows[i].style.display = hit ? '' : 'none';
    if (hit) shown++;
  }
  var empty = document.getElementById(tableId.replace('-table', '-empty'));
  if (empty) empty.style.display = shown ? 'none' : '';
}
{% endraw %}
</script>
{% endblock %}"""

# --- Leads (new page, same design language) -----------------------------------
LEADS = """{% extends "shell" %}
{% block head %}<style>
  .scroll-table { max-height: 520px; overflow-y: auto; }
  .scroll-table thead th { position: sticky; top: 0; z-index: 1; background: #102035; }
</style>{% endblock %}
{% block content %}
<h1 class="page-title">Leads</h1>
<p class="lede">Every contact the bot has captured, newest first — the same records pushed to your Google Sheet and Telegram. <strong>Interest</strong> and <strong>remarks</strong> are what the AI understood from the conversation, written for whoever makes the call.</p>

<section class="card">
  <h2>Captured leads{% if leads %} ({{ leads | length }}){% endif %}</h2>
  {% if leads %}
  <p class="hint">Showing the most recent {{ limit }}. <a href="/leads.csv">Download all as CSV</a> — opens in Excel with Bangla intact.</p>
  <input type="search" placeholder="Search by name, phone, course…" oninput="filterTable(this, 'leads-table')" style="margin:14px 0">
  <div class="table-wrap scroll-table"><table id="leads-table" style="min-width:900px">
  <thead><tr><th>When</th><th>Name</th><th>Phone</th><th>Interested in</th><th>Remarks</th><th></th></tr></thead>
  <tbody>
  {% for l in leads %}
  <tr>
    <td style="white-space:nowrap">{{ l.when }}</td>
    <td>{{ l.name }}</td>
    <td style="white-space:nowrap"><code>{{ l.phone }}</code></td>
    <td>{% if l.interest %}{{ l.interest }}{% else %}<span style="color:var(--muted-2)">—</span>{% endif %}</td>
    <td>{% if l.remarks %}{{ l.remarks }}{% else %}<span style="color:var(--muted-2)">—</span>{% endif %}</td>
    <td>{% if l.url %}<a class="btn-ghost" style="padding:5px 12px;font-size:13px;text-decoration:none" href="{{ l.url }}" target="_blank" rel="noopener">Open chat</a>{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody></table></div>
  <p class="hint" id="leads-empty" style="display:none;margin-top:12px">No lead matches that search.</p>
  {% else %}
  <p class="hint">No leads captured yet. They appear here the moment the bot saves a customer's name and number.</p>
  {% endif %}
</section>
{% endblock %}

{% block scripts %}
<script>
{% raw %}
function filterTable(input, tableId) {
  var q = input.value.trim().toLowerCase();
  var rows = document.querySelectorAll('#' + tableId + ' tbody tr');
  var shown = 0;
  for (var i = 0; i < rows.length; i++) {
    var hit = rows[i].textContent.toLowerCase().indexOf(q) !== -1;
    rows[i].style.display = hit ? '' : 'none';
    if (hit) shown++;
  }
  var empty = document.getElementById(tableId.replace('-table', '-empty'));
  if (empty) empty.style.display = shown ? 'none' : '';
}
{% endraw %}
</script>
{% endblock %}"""

env = Environment(
    loader=DictLoader({
        "shell": SHELL,
        "sidebar": SIDEBAR,
        "login": LOGIN,
        "kb": KB,
        "prompt": PROMPT,
        "blocked": BLOCKED,
        "leads": LEADS,
        "forbidden": FORBIDDEN,
    }),
    autoescape=select_autoescape(default=True, default_for_string=True),
)
env.globals["page_style"] = PAGE_STYLE
env.globals["login_style"] = LOGIN_STYLE
