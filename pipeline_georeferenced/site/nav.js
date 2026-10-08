// Shared top bar for every page: the DepthWizard name and links between the
// pages. Load with <script src="/site/nav.js" defer></script>.
//
// Sets --nav-h on :root to the bar's height. Ordinary pages get that much
// top padding; full-screen pages (3D viewer, 2D map) set <body
// data-nav="overlay"> and push their fixed layers down by var(--nav-h). The DEM Court and validation
// links need a job: the page's own ?job= if it has one, otherwise the newest
// finished job from GET /jobs.
(function () {
  const NAV_H = 48;
  const LINKS = [
    { key: 'dashboard', label: 'Dashboard', href: '/dashboard.html', match: p => p.startsWith('/dashboard') },
    { key: 'home', label: 'New job', href: '/', match: p => p === '/' || p === '/index.html' },
    { key: 'history', label: 'History', href: '/history.html', match: p => p.startsWith('/history') },
    { key: 'map', label: '2D map', href: '/leaflet_pitch/index.html', match: p => p.startsWith('/leaflet_pitch') },
    { key: 'viewer', label: '3D terrain', href: '/3d_visualization/index.html', match: p => p.startsWith('/3d_visualization') },
    { key: 'court', label: 'DEM Court', href: '/dem_court/index.html', match: p => p.startsWith('/dem_court'), job: true },
    { key: 'validation', label: 'Validation', href: '/validation/index.html', match: p => p.startsWith('/validation'), job: true },
  ];

  const css = `
    :root { --nav-h: ${NAV_H}px; }
    #dw-nav { position: fixed; top: 0; left: 0; right: 0; height: ${NAV_H}px; z-index: 5000; box-sizing: border-box;
      display: flex; align-items: center; gap: 18px; padding: 0 16px; overflow-x: auto; scrollbar-width: none;
      background: rgba(11,12,16,0.92); backdrop-filter: blur(8px); border-bottom: 1px solid rgba(255,255,255,0.08);
      font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
    #dw-nav::-webkit-scrollbar { display: none; }
    #dw-nav .brand { display: flex; align-items: center; gap: 8px; color: #e8eaed; text-decoration: none;
      font-weight: 700; font-size: 15px; letter-spacing: 0.2px; white-space: nowrap; }
    #dw-nav .brand svg { flex: none; }
    #dw-nav .brand small { color: #9aa0a6; font-weight: 500; font-size: 11px; }
    #dw-nav .dw-links { display: flex; gap: 2px; margin-left: auto; }
    #dw-nav .dw-links a { display: inline-block; margin: 0; color: #9aa0a6; text-decoration: none; font-size: 13px; font-weight: 500; white-space: nowrap;
      padding: 6px 10px; border-radius: 6px; }
    #dw-nav .dw-links a:hover { color: #e8eaed; background: rgba(255,255,255,0.06); }
    #dw-nav .dw-links a.active { color: #4fc3f7; background: rgba(79,195,247,0.12); }
    #dw-nav .dw-links a.nojob { opacity: 0.45; }
    @media (max-width: 720px) { #dw-nav .brand small { display: none; } }
  `;
  const logo = `<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M2 19 L8 9 L12 14 L16 6 L22 19 Z" fill="#4fc3f7" opacity="0.9"/>
    <path d="M2 19 L8 9 L12 14 L16 6 L22 19" fill="none" stroke="#e8eaed" stroke-width="1.2" stroke-linejoin="round"/></svg>`;

  function build() {
    // ?embed=1: the page is framed inside the dashboard, which has its own bar.
    if (new URLSearchParams(location.search).get('embed') === '1') return;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    const path = location.pathname;
    const jobHere = new URLSearchParams(location.search).get('job');
    const nav = document.createElement('nav');
    nav.id = 'dw-nav';
    nav.innerHTML = `<a class="brand" href="/dashboard.html">${logo}<span>DepthWizard</span><small>SIH · Team StarOps</small></a>` +
      `<div class="dw-links">${LINKS.map(l =>
        `<a data-key="${l.key}" href="${l.href}${l.job && jobHere ? `?job=${encodeURIComponent(jobHere)}` : ''}"` +
        `${l.match(path) ? ' class="active"' : ''}>${l.label}</a>`).join('')}</div>`;
    document.body.prepend(nav);
    // Full-screen pages mark <body data-nav="overlay"> and offset their own fixed layers by --nav-h.
    if (document.body.dataset.nav !== 'overlay') document.body.style.paddingTop = `${NAV_H}px`;

    if (jobHere) return;
    const jobLinks = LINKS.filter(l => l.job).map(l => [l, nav.querySelector(`[data-key="${l.key}"]`)]);
    fetch('/jobs').then(r => (r.ok ? r.json() : [])).then(list => {
      const job = Array.isArray(list) && list.find(j => j.status === 'done');
      for (const [l, a] of jobLinks) {
        if (job) a.href = `${l.href}?job=${encodeURIComponent(job.id)}`;
        else { a.classList.add('nojob'); a.title = 'Process an AOI first'; }
      }
    }).catch(() => {});
  }

  if (document.body) build();
  else document.addEventListener('DOMContentLoaded', build);
})();
