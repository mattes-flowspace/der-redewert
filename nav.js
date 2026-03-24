/**
 * Shared navigation with dropdowns for der-redewert.
 * Add <script src="nav.js"></script> before </body> on every page.
 * Existing <nav> inside <header> and .mobile-nav are replaced.
 */
(function () {

  /* ── Inject dropdown CSS ── */
  const style = document.createElement('style');
  style.textContent = `
    /* ─── DROPDOWN NAV ─────────────────────────────────── */
    header nav { position: relative; }
    .nav-dropdown { position: relative; }
    .nav-dropdown > a { display: flex; align-items: center; gap: 4px; }
    .nav-dropdown > a::after {
      content: '▾'; font-size: 11px;
      transition: transform 0.2s;
    }
    .nav-dropdown.open > a::after { transform: rotate(180deg); }
    .dropdown-menu {
      display: none;
      position: absolute; top: 100%; left: 50%;
      transform: translateX(-50%);
      padding-top: 12px;
      background: transparent;
      min-width: 210px;
      z-index: 200;
    }
    .dropdown-menu-inner {
      background: #fff;
      box-shadow: 0 4px 16px rgba(0,0,0,0.12);
      display: flex; flex-direction: column;
    }
    .nav-dropdown.open .dropdown-menu { display: block; }
    .dropdown-menu a {
      font-family: var(--font-body) !important;
      font-size: 13px !important;
      color: #1a1a1a !important;
      padding: 11px 18px;
      border-bottom: 1px solid rgba(0,0,0,0.06);
      letter-spacing: 0.02em;
      white-space: nowrap;
      display: block;
    }
    .dropdown-menu a:last-child { border-bottom: none; }
    .dropdown-menu a:hover { background: var(--mint); text-decoration: none !important; }

    /* ─── MOBILE NAV SUB-LINKS ──────────────────────────── */
    .mobile-nav .mobile-sub {
      padding-left: 18px;
      font-size: 14px !important;
      color: #444 !important;
    }
    .mobile-nav .mobile-group-label {
      font-weight: 700;
    }
  `;
  document.head.appendChild(style);

  /* ── Active page detection ── */
  const page = window.location.pathname.split('/').pop() || 'index.html';
  const coachingPages = ['coaching.html','life-coaching.html','business-coaching.html','teamcoaching.html','paarcoaching.html'];
  const mediationPages = ['mediation.html','ablauf-mediation.html','paarmediation.html'];
  const ueberPages = ['ueber-mich.html','philosophie.html','kundenstimmen.html'];

  function activeClass(pages) {
    return pages.includes(page) ? ' class="active"' : '';
  }
  function linkActive(href) {
    const p = href.split('/').pop().split('#')[0];
    return p === page ? ' class="active"' : '';
  }

  function makeMenu(items) {
    return `<div class="dropdown-menu"><div class="dropdown-menu-inner">${items.map(([href, label]) =>
      `<a href="${href}"${linkActive(href)}>${label}</a>`).join('')}</div></div>`;
  }

  /* ── Desktop nav replacement ── */
  const headerNav = document.querySelector('header nav');
  if (headerNav) {
    headerNav.innerHTML = `
      <a href="index.html"${linkActive('index.html')}>Startseite</a>
      <div class="nav-dropdown">
        <a href="coaching.html"${activeClass(coachingPages)}>Coaching</a>
        ${makeMenu([
          ['life-coaching.html','Systemisches Coaching'],
          ['business-coaching.html','Business Coaching'],
          ['teamcoaching.html','Teamcoaching'],
          ['paarcoaching.html','Paarcoaching']
        ])}
      </div>
      <div class="nav-dropdown">
        <a href="mediation.html"${activeClass(mediationPages)}>Mediation</a>
        ${makeMenu([
          ['ablauf-mediation.html','Ablauf einer Mediation'],
          ['paarmediation.html','Paarmediation']
        ])}
      </div>
      <a href="seminare.html"${linkActive('seminare.html')}>Seminare</a>
      <div class="nav-dropdown">
        <a href="ueber-mich.html"${activeClass(ueberPages)}>Über mich</a>
        ${makeMenu([
          ['philosophie.html','Philosophie'],
          ['kundenstimmen.html','Kundenstimmen']
        ])}
      </div>
      <a href="index.html#kontakt">Kontakt</a>
    `;

    /* ── JS hover with close-delay so menu sticks ── */
    headerNav.querySelectorAll('.nav-dropdown').forEach(function(dd) {
      let closeTimer;
      function open()  { clearTimeout(closeTimer); dd.classList.add('open'); }
      function close() { closeTimer = setTimeout(function(){ dd.classList.remove('open'); }, 250); }
      dd.addEventListener('mouseenter', open);
      dd.addEventListener('mouseleave', close);
      dd.querySelector('.dropdown-menu').addEventListener('mouseenter', open);
      dd.querySelector('.dropdown-menu').addEventListener('mouseleave', close);
    });
  }

  /* ── Mobile nav replacement ── */
  const mobileNav = document.getElementById('mobileNav');
  if (mobileNav) {
    mobileNav.innerHTML = `
      <a href="index.html">Startseite</a>
      <a href="coaching.html" class="mobile-group-label">Coaching</a>
      <a href="life-coaching.html" class="mobile-sub">Systemisches Coaching</a>
      <a href="business-coaching.html" class="mobile-sub">Business Coaching</a>
      <a href="teamcoaching.html" class="mobile-sub">Teamcoaching</a>
      <a href="paarcoaching.html" class="mobile-sub">Paarcoaching</a>
      <a href="mediation.html" class="mobile-group-label">Mediation</a>
      <a href="ablauf-mediation.html" class="mobile-sub">Ablauf einer Mediation</a>
      <a href="paarmediation.html" class="mobile-sub">Paarmediation</a>
      <a href="seminare.html">Seminare</a>
      <a href="ueber-mich.html" class="mobile-group-label">Über mich</a>
      <a href="philosophie.html" class="mobile-sub">Philosophie</a>
      <a href="kundenstimmen.html" class="mobile-sub">Kundenstimmen</a>
      <a href="index.html#kontakt">Kontakt</a>
    `;
  }

})();
