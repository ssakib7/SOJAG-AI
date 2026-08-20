"""Admin panel CSS, copied verbatim from the Node bot's admin.js.

PAGE_STYLE is the panel shell (navy/cyan/orange SOJAG AI theme, sidebar layout,
spreadsheet-style course tables, sticky save bar). LOGIN_STYLE is the standalone
login screen. Kept as plain strings so the design stays byte-identical to the UI
the team already knows.
"""

PAGE_STYLE = r"""
  :root {
    --navy: #070E1B; --navy-mid: #0C1929; --panel: rgba(12, 22, 38, 0.72);
    --panel-solid: #0C1929; --ink: #EAF2FB; --muted: #8BAAC8; --muted-2: #5A7798;
    --line: rgba(0, 202, 255, 0.10); --line-strong: rgba(139, 170, 200, 0.20);
    --accent: #00CAFF; --accent-ink: #00b0e0; --orange: #F5821E;
    --red: #ff6b6b; --ok: #4fd6a0; --ok-bg: rgba(79, 214, 160, 0.08);
    --serif: 'Inter', ui-sans-serif, system-ui, sans-serif;
    --sans: 'Hind Siliguri', 'Inter', ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans Bengali", sans-serif;
    --radius: 12px;
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body { margin: 0; background: var(--navy); color: var(--ink); font-family: var(--sans);
    font-size: 15px; line-height: 1.55;
    background-image: radial-gradient(1200px 600px at 80% -10%, rgba(0,202,255,.06), transparent 60%),
      radial-gradient(900px 500px at -10% 110%, rgba(245,130,30,.05), transparent 55%); }
  a { color: var(--accent); }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em;
    background: rgba(0,202,255,.10); color: #b9e9ff; padding: 1px 5px; border-radius: 4px; }

  /* Layout: fixed sidebar + content */
  .layout { display: grid; grid-template-columns: 248px minmax(0, 1fr); min-height: 100vh; }
  .sidebar { position: sticky; top: 0; align-self: start; height: 100vh; overflow-y: auto;
    background: rgba(9, 18, 32, 0.85); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
    border-right: 1px solid var(--line); padding: 22px 16px;
    display: flex; flex-direction: column; gap: 4px; }
  .brand { display: flex; align-items: center; gap: 11px; padding: 2px 6px 18px;
    border-bottom: 1px solid var(--line); margin-bottom: 16px; }
  .mark { width: 38px; height: 38px; flex: none; border-radius: 10px;
    background: linear-gradient(145deg, #0B1929, #0E2040);
    border: 1px solid rgba(0, 202, 255, 0.28);
    box-shadow: 0 0 0 1px rgba(245,130,30,.12), 0 0 18px rgba(0,202,255,.14);
    color: #fff; display: grid; place-items: center; font-size: 19px; }
  .brand-name { font-family: var(--serif); font-weight: 800; font-size: 15px; line-height: 1.15; letter-spacing: .5px; }
  .brand-name .ai { color: var(--accent); }
  .brand-sub { font-size: 11px; color: var(--orange); letter-spacing: 1.5px; text-transform: uppercase; font-weight: 600; }
  .nav { display: flex; flex-direction: column; gap: 1px; }
  .nav a { display: block; padding: 8px 11px; border-radius: 8px; color: var(--muted);
    text-decoration: none; font-size: 14px; font-weight: 500; transition: background .15s, color .15s; }
  .nav a:hover { background: rgba(0,202,255,.08); color: var(--ink); }
  .nav a.nav-active { background: rgba(0,202,255,.12); color: var(--ink); font-weight: 600; }
  .nav-label { font-size: 10.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--muted-2); padding: 14px 11px 5px; }
  .nav a.nav-sub { padding: 5px 11px 5px 22px; font-size: 13px; color: var(--muted-2); }
  .nav a.nav-sub:hover { color: var(--ink); }
  .nav-foot { margin-top: auto; padding-top: 14px; border-top: 1px solid var(--line); }
  .who { font-size: 11.5px; color: var(--muted-2); margin-bottom: 8px; }
  .nav-foot form { margin: 0; }
  .nav-foot button { width: 100%; }

  .main { padding: 38px 44px 120px; max-width: 940px; }
  .page-title { font-family: var(--serif); font-size: 28px; font-weight: 700; letter-spacing: -.01em; margin: 0 0 6px; }
  .lede { color: var(--muted); margin: 0 0 24px; font-size: 14.5px; max-width: 62ch; }

  @media (max-width: 860px) {
    .layout { grid-template-columns: 1fr; }
    .sidebar { position: static; height: auto; flex-direction: row; flex-wrap: wrap; align-items: center; gap: 8px; padding: 14px 18px; }
    .brand { border: 0; margin: 0; padding: 0; flex: 1 0 auto; }
    .nav { flex-direction: row; flex-wrap: wrap; }
    .nav-foot { margin: 0; padding: 0; border: 0; }
    .nav-foot button { width: auto; }
    .main { padding: 22px 18px 120px; }
  }

  /* Section panels */
  [id] { scroll-margin-top: 16px; }
  .card { background: var(--panel); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border: 1px solid var(--line); border-radius: var(--radius);
    padding: 22px 22px 24px; margin: 0 0 18px;
    box-shadow: 0 1px 0 0 rgba(255,255,255,.03) inset, 0 0 40px rgba(0,0,0,.3); }
  .card h2 { font-family: var(--serif); font-size: 18px; font-weight: 700; margin: 0; }
  .card .hint { color: var(--muted-2); font-size: 13px; margin: 4px 0 0; }
  .card h2 + .grid, .card h2 + .table-wrap, .card .hint + .grid,
  .card .hint + label, .card h2 + label { margin-top: 16px; }

  label { display: block; font-size: 12.5px; font-weight: 600; color: var(--muted); margin: 16px 0 6px;
    letter-spacing: .005em; }
  label:first-of-type { margin-top: 0; }
  input, textarea, select { width: 100%; font: inherit; color: var(--ink); background: rgba(255,255,255,0.04);
    border: 1px solid var(--line-strong); border-radius: 8px; padding: 9px 11px;
    transition: border-color .15s, box-shadow .15s, background .15s; }
  input::placeholder, textarea::placeholder { color: var(--muted-2); }
  input:focus, textarea:focus { outline: none; border-color: var(--accent);
    background: rgba(0,202,255,.05); box-shadow: 0 0 0 3px rgba(0,202,255,.15); }
  textarea { min-height: 130px; resize: vertical; line-height: 1.55; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; }
  @media (max-width: 560px) { .grid { grid-template-columns: 1fr; } }

  /* Course table — spreadsheet feel: borderless cells until hover/focus */
  .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
  table { width: 100%; border-collapse: collapse; min-width: 800px; font-size: 14px; }
  thead th { background: rgba(0,202,255,.05); text-align: left; font-size: 11px; font-weight: 700;
    letter-spacing: .05em; text-transform: uppercase; color: var(--muted);
    padding: 11px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; }
  tbody td { padding: 5px 7px; border-bottom: 1px solid var(--line); vertical-align: middle; }
  tbody tr:last-child td { border-bottom: 0; }
  td input { border: 1px solid transparent; background: transparent; border-radius: 6px; padding: 7px 9px; font-size: 13.5px; }
  td input:hover { border-color: var(--line-strong); }
  td input:focus { background: rgba(0,202,255,.06); }
  .col-name { min-width: 240px; }
  .col-num input { text-align: right; }
  .price-pair { display: flex; align-items: center; gap: 4px; }
  .price-pair input { width: 78px; }
  .price-pair .sep { color: var(--muted-2); }

  /* Buttons */
  button { font: inherit; font-weight: 600; font-size: 14px; cursor: pointer; border-radius: 8px;
    border: 1px solid transparent; padding: 9px 16px; transition: background .15s, border-color .15s, box-shadow .15s, transform .15s; }
  .btn-primary { background: linear-gradient(135deg, var(--orange) 0%, #d96d0e 42%, #0098c0 72%, var(--accent) 100%);
    background-size: 220% 220%; color: #fff; box-shadow: 0 4px 18px rgba(245,130,30,.28); }
  .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 8px 26px rgba(245,130,30,.34), 0 8px 26px rgba(0,202,255,.18); }
  .btn-ghost { background: rgba(255,255,255,0.04); color: var(--ink); border-color: var(--line-strong); }
  .btn-ghost:hover { background: rgba(0,202,255,.08); border-color: var(--accent); }
  .add-row { background: rgba(0,202,255,.04); color: var(--accent); border: 1px dashed var(--line-strong);
    font-size: 13px; padding: 8px 14px; margin-top: 14px; }
  .add-row:hover { border-color: var(--accent); background: rgba(0,202,255,.10); }
  .icon-btn { background: transparent; color: var(--muted-2); border: 1px solid transparent;
    width: 30px; height: 30px; padding: 0; line-height: 1; font-size: 14px; border-radius: 6px; }
  .icon-btn:hover { background: rgba(255,107,107,.12); color: var(--red); border-color: rgba(255,107,107,.3); }

  /* Add-course / add-column actions under each table */
  .table-actions { display: flex; gap: 10px; flex-wrap: wrap; }

  /* Custom (admin-added) free-form sections */
  .custom-head { display: flex; align-items: center; gap: 10px; margin: 0 0 4px; }
  .custom-title { font-family: var(--serif); font-size: 18px; font-weight: 700; color: var(--ink);
    background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px 9px; }
  .custom-title::placeholder { color: var(--muted-2); font-weight: 600; }
  .custom-title:hover { border-color: var(--line-strong); }
  .custom-title:focus { outline: none; background: rgba(0,202,255,.06); border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(0,202,255,.15); }
  .custom-head .icon-btn { flex: none; }
  .custom-empty { color: var(--muted); font-size: 14px; margin: 0 0 14px; }
  #sec-custom > .add-row { margin-top: 0; }

  /* System prompt — long instructions, so a tall editor with a monospace feel */
  textarea.prompt-input { min-height: 340px; font-size: 13.5px; line-height: 1.6; }

  /* Examples & scenarios group (admin-added prompt sections) */
  .group-head { font-family: var(--serif); font-size: 18px; font-weight: 700; margin: 26px 0 2px; }
  .group-sub { color: var(--muted); font-size: 13.5px; margin: 0 0 14px; max-width: 72ch; }
  #sec-scenarios > .add-row { margin-top: 0; }

  /* Custom (admin-added) text columns */
  .extra-col { min-width: 130px; }
  .col-head { display: flex; align-items: center; gap: 4px; }
  .col-head input { font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
    color: var(--muted); background: transparent; border: 1px solid transparent; padding: 5px 6px; min-width: 86px; }
  .col-head input:hover { border-color: var(--line-strong); }
  .col-head input:focus { background: rgba(0,202,255,.06); color: var(--ink); }
  .col-del { background: transparent; color: var(--muted-2); border: 0; width: 20px; height: 20px;
    padding: 0; font-size: 12px; line-height: 1; border-radius: 5px; flex: none; }
  .col-del:hover { background: rgba(255,107,107,.15); color: var(--red); }

  /* Sticky save bar — starts after the sidebar so it never covers the Log out button */
  .savebar { position: fixed; left: 248px; right: 0; bottom: 0; z-index: 25;
    background: rgba(9, 18, 32, 0.86); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-top: 1px solid var(--line); }
  .savebar-inner { padding: 12px 44px; display: flex; align-items: center; gap: 14px; }
  .savebar .note { color: var(--muted); font-size: 13px; }
  .savebar .spacer { flex: 1; }
  @media (max-width: 860px) { .savebar { left: 0; } .savebar-inner { padding: 12px 18px; } }

  /* Flash */
  .flash { display: flex; align-items: center; gap: 9px; background: var(--ok-bg);
    border: 1px solid rgba(79,214,160,.3); color: var(--ok); padding: 12px 14px; border-radius: 8px;
    margin: 0 0 20px; font-size: 14px; }"""

LOGIN_STYLE = r"""
  :root {
    --navy:        #070E1B;
    --navy-mid:    #0C1929;
    --navy-card:   rgba(9, 18, 32, 0.88);
    --orange:      #F5821E;
    --orange-dim:  rgba(245, 130, 30, 0.22);
    --cyan:        #00CAFF;
    --cyan-dim:    rgba(0, 202, 255, 0.18);
    --white:       #FFFFFF;
    --silver:      #8BAAC8;
    --muted:       #3D5A78;
    --input-bg:    rgba(255,255,255,0.04);
    --input-bdr:   rgba(255,255,255,0.09);
    --radius-card: 22px;
    --radius-inp:  12px;
    --f-bn:        'Hind Siliguri', sans-serif;
    --f-lat:       'Inter', sans-serif;
  }

  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  html, body {
    height: 100%;
    background: var(--navy);
    font-family: var(--f-bn);
    color: var(--white);
    overflow: hidden;
  }

  /* Animated circuit canvas */
  #cvs { position: fixed; inset: 0; width: 100%; height: 100%; z-index: 0; }

  /* Page shell */
  .page {
    position: relative;
    z-index: 10;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px 16px;
    overflow-y: auto;
  }

  /* Card */
  .card {
    width: 100%;
    max-width: 440px;
    background: var(--navy-card);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(0, 202, 255, 0.13);
    border-radius: var(--radius-card);
    padding: 44px 36px 36px;
    position: relative;
    box-shadow:
      0 2px 0   0 rgba(255,255,255,0.04) inset,
      0 0 60px  0 rgba(0,0,0,0.6),
      0 0 120px 0 rgba(0,202,255,0.04);
    animation: cardIn .55s cubic-bezier(.22,.68,0,1.2) both;
  }

  @keyframes cardIn {
    from { opacity:0; transform: translateY(28px) scale(.97); }
    to   { opacity:1; transform: translateY(0)    scale(1);   }
  }

  /* top shimmer line */
  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 10%; right: 10%; height: 1.5px;
    background: linear-gradient(90deg, transparent, var(--orange) 30%, var(--cyan) 70%, transparent);
    border-radius: 0 0 4px 4px;
  }

  /* Logo */
  .logo-wrap { display: flex; flex-direction: column; align-items: center; gap: 10px; margin-bottom: 28px; }
  .logo-box {
    width: 68px; height: 68px;
    background: linear-gradient(145deg, #0B1929, #0E2040);
    border-radius: 17px;
    border: 1px solid rgba(0, 202, 255, 0.28);
    display: flex; align-items: center; justify-content: center;
    position: relative;
    box-shadow: 0 0 0 1px rgba(245,130,30,.12), 0 0 28px rgba(0,202,255,.12), 0 6px 30px rgba(0,0,0,.5);
  }
  .logo-box::after {
    content: '';
    position: absolute;
    inset: -3px; border-radius: 20px;
    background: conic-gradient(from 190deg, var(--orange) 0deg 80deg, transparent 80deg 260deg, var(--cyan) 260deg 340deg, transparent 340deg 360deg);
    z-index: -1; opacity: .55; animation: spin 6s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .logo-brain { font-size: 32px; line-height: 1; filter: drop-shadow(0 0 8px rgba(0,202,255,.6)); }
  .logo-name {
    font-family: var(--f-lat); font-size: 26px; font-weight: 800; letter-spacing: 1.5px;
    color: var(--white); display: flex; align-items: center; gap: 3px; line-height: 1;
  }
  .logo-name .ai { color: var(--cyan); }
  .logo-name .dot { font-size: 13px; color: var(--orange); margin: 0 1px; }
  .logo-badge {
    font-family: var(--f-lat); font-size: 11px; font-weight: 600; letter-spacing: 2.4px;
    text-transform: uppercase; color: var(--orange); opacity: .9;
  }

  /* Welcome */
  .welcome { text-align: center; margin-bottom: 30px; }
  .welcome h1 { font-size: 23px; font-weight: 700; color: var(--white); margin-bottom: 7px; line-height: 1.35; }
  .welcome p { font-size: 14px; color: var(--silver); line-height: 1.65; }
  .welcome p strong { color: var(--cyan); font-weight: 600; }

  /* Form */
  .field { margin-bottom: 18px; }
  .field label { display: block; font-size: 13.5px; font-weight: 500; color: var(--silver); margin-bottom: 7px; padding-left: 2px; }
  .inp-wrap { position: relative; display: flex; align-items: center; }
  .inp-icon { position: absolute; left: 14px; color: var(--muted); font-size: 15px; pointer-events: none; transition: color .25s; }
  .inp-wrap:focus-within .inp-icon { color: var(--cyan); }
  input.inp {
    width: 100%;
    padding: 13.5px 14px 13.5px 42px;
    background: var(--input-bg);
    border: 1px solid var(--input-bdr);
    border-radius: var(--radius-inp);
    color: var(--white);
    font-size: 14.5px;
    font-family: var(--f-bn);
    outline: none;
    transition: border-color .25s, box-shadow .25s, background .25s;
    -webkit-appearance: none;
  }
  input.inp::placeholder { color: var(--muted); font-size: 13.5px; }
  input.inp:focus { border-color: var(--cyan); background: rgba(0,202,255,.05); box-shadow: 0 0 0 3px rgba(0,202,255,.15); }
  .inp.pr { padding-right: 44px; }
  .pw-toggle {
    position: absolute; right: 13px; background: none; border: none; color: var(--muted);
    cursor: pointer; font-size: 15px; padding: 4px; transition: color .25s;
  }
  .pw-toggle:hover { color: var(--cyan); }

  /* Row */
  .row-opts { display: flex; justify-content: space-between; align-items: center; margin-bottom: 26px; flex-wrap: wrap; gap: 10px; }
  .chk-label { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 13.5px; color: var(--silver); user-select: none; }
  .chk-label input[type=checkbox] { width: 16px; height: 16px; accent-color: var(--cyan); cursor: pointer; border-radius: 4px; }
  .forgot { font-size: 13.5px; color: var(--cyan); text-decoration: none; transition: color .25s; }
  .forgot:hover { color: var(--orange); }

  /* Button */
  .btn-login {
    width: 100%; padding: 15px; border: none; border-radius: var(--radius-inp);
    font-family: var(--f-bn); font-size: 17px; font-weight: 700; color: var(--white); cursor: pointer;
    background: linear-gradient(135deg, var(--orange) 0%, #d96d0e 40%, #0098c0 70%, var(--cyan) 100%);
    background-size: 250% 250%; animation: btnShift 5s ease infinite;
    box-shadow: 0 4px 22px rgba(245,130,30,.35);
    transition: transform .2s, box-shadow .25s; position: relative; overflow: hidden; letter-spacing: .3px;
  }
  @keyframes btnShift { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
  .btn-login:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(245,130,30,.4), 0 8px 32px rgba(0,202,255,.2); }
  .btn-login:active { transform: translateY(0); }
  .btn-login:disabled { opacity: .65; cursor: not-allowed; transform: none; }

  /* Divider */
  .divider { display: flex; align-items: center; gap: 10px; margin: 22px 0; }
  .divider::before, .divider::after { content:''; flex:1; height:1px; background: var(--input-bdr); }
  .divider span { font-size: 12px; color: var(--muted); white-space: nowrap; }

  /* Footer */
  .card-foot { text-align: center; margin-top: 20px; font-size: 13.5px; color: var(--muted); }
  .card-foot a { color: var(--cyan); text-decoration: none; font-weight: 500; }
  .card-foot a:hover { color: var(--orange); }

  /* Bottom bar */
  .bottom-bar { margin-top: 20px; text-align: center; font-family: var(--f-lat); font-size: 11.5px; color: var(--muted); letter-spacing: .3px; opacity: .75; }

  /* Field-level error state */
  .err-msg { display: none; font-size: 12.5px; color: #ff6b6b; margin-top: 6px; padding-left: 2px; }
  .field.has-error input.inp { border-color: #ff6b6b; box-shadow: 0 0 0 3px rgba(255,107,107,.15); }
  .field.has-error .err-msg { display: block; }

  /* Server-side error banner */
  .server-err {
    background: rgba(255,107,107,.08); border: 1px solid rgba(255,107,107,.35); color: #ff6b6b;
    font-size: 13.5px; font-weight: 500; padding: 11px 14px; border-radius: var(--radius-inp);
    margin-bottom: 22px; display: flex; align-items: center; gap: 8px;
  }

  /* Sparkle badge top-right */
  .badge-ai {
    position: absolute; top: 18px; right: 20px;
    font-family: var(--f-lat); font-size: 10px; font-weight: 700; letter-spacing: 1px; color: var(--cyan);
    border: 1px solid rgba(0,202,255,.3); border-radius: 20px; padding: 3px 9px;
    display: flex; align-items: center; gap: 4px; opacity: .8;
  }
  .badge-ai .dot-pulse { width: 6px; height: 6px; background: var(--cyan); border-radius: 50%; animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity:1; transform: scale(1); } 50% { opacity:.4; transform: scale(.7); } }

  /* Responsive */
  @media (max-width: 480px) {
    .card { padding: 38px 22px 30px; }
    .welcome h1 { font-size: 20px; }
    .btn-login { font-size: 16px; }
    .row-opts { flex-direction: column; align-items: flex-start; }
  }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; } }"""
