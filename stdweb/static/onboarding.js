(function () {
  var KEY = 'stdweb.onboard.v1';
  var cfg = {};
  try {
    cfg = JSON.parse(document.getElementById('onboard-config').textContent);
  } catch (e) {
    cfg = { demo_task_id: 4853, veteran: false, done: {}, next: 'upload', authenticated: false };
  }

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var staticBase = (document.getElementById('onboard-static') || {}).value || '/static/onboarding/';
  var demo = cfg.demo_task_id || 4853;

  var STEPS = [
    {
      key: 'welcome',
      title: 'Why STDWeb?',
      text: 'Turn any FITS frame into a Gaia-calibrated magnitude in minutes — in the browser, nothing to install. STDWeb solves the astrometry, fits the photometric zero-point against Gaia, and handles the parts that trip up a manual reduction: colour terms and blended stars. You get a magnitude with a real error bar, plus forced photometry, template subtraction, and one-click alert publishing. This walkthrough uses a real RAPAS reduction (Nova Sge).',
      gif: 'teaser.gif',
      still: 'results.png',
    },
    {
      key: 'login',
      title: '1 · Log in',
      text: 'Sign in with your email. There is nothing to install and nothing to configure — the whole pipeline runs on the server and shows up in your browser.',
      gif: 'login.gif',
      still: 'login.png',
      href: '/login/',
    },
    {
      key: 'upload',
      title: '2 · Upload a FITS',
      text: 'Pick your file and click Upload. Each upload becomes one task: one image, one page, one result. You can name the target right here, or on the next screen.',
      gif: 'upload.gif',
      still: 'upload.png',
      href: '/',
      onboard: 'upload',
    },
    {
      key: 'target',
      title: 'Your first target — how to set it',
      text: 'Tell STDWeb what you came to measure. In the Target field, type a name that TNS or Simbad can resolve (e.g. "Nova Sge 2024", "AT2024xyz") — or paste the position as "RA Dec". One target per line; degrees or sexagesimal both work. That sky position is what forced photometry measures, even when the source is faint or sits on top of another star.',
      gif: 'target.gif',
      still: 'target.png',
      href: '/tasks/' + demo,
      onboard: 'target',
    },
    {
      key: 'inspect',
      title: 'Measure it · 1 — Inspect the frame',
      text: 'On the task page, find the "Initial inspection and masking" panel. STDWeb pre-fills gain, saturation and filter from the FITS header and resolves your target name to coordinates, so usually you change nothing — just click the blue "Run initial inspection" button. Only fill Gain (e-/ADU) yourself if your header lacks it. The step builds the cosmics/bad-pixel mask and solves the WCS. Tip: "Open demo task" below jumps straight to that button and highlights it.',
      gif: 'inspect.gif',
      still: 'inspect.png',
      href: '/tasks/' + demo,
      onboard: 'inspect',
    },
    {
      key: 'photometry',
      title: 'Measure it · 2 — Run photometry',
      text: 'Confirm the auto-detected filter, then Run photometry. Keep the defaults on: colour term and blend filtering matter, because amateur/RAPAS filters are not a perfect match to Gaia and fields are often crowded. STDWeb detects the stars and fits the Gaia zero-point.',
      gif: 'photometry.gif',
      still: 'photometry.png',
      href: '/tasks/' + demo,
      onboard: 'photometry',
    },
    {
      key: 'results',
      title: 'Measure it · 3 — Read the magnitude',
      text: 'The payoff: a Gaia-calibrated magnitude with an error bar for your target, with the measurement table and target cutout right below. If this is a follow-up, publish it to Telegram in one click.',
      gif: 'results.gif',
      still: 'results.png',
      href: '/tasks/' + demo,
      onboard: 'target',
    },
    {
      key: 'subtraction',
      title: 'Go further · Subtraction (optional)',
      text: 'Subtract a survey template (Pan-STARRS, …) and re-measure your target on the difference image — the clean way to pull a transient out of its host galaxy.',
      gif: 'subtraction.gif',
      still: 'subtraction.png',
      href: '/tasks/' + demo,
      onboard: 'subtraction',
    },
  ];

  function load() {
    try {
      return JSON.parse(localStorage.getItem(KEY) || '{}');
    } catch (e) {
      return {};
    }
  }

  function save(state) {
    localStorage.setItem(KEY, JSON.stringify(state));
  }

  var MEDIA_VER = '6';
  function media(step) {
    var file = reduce ? (step.still || step.gif) : (step.gif || step.still);
    return staticBase + file + '?v=' + MEDIA_VER;
  }

  var overlay = document.getElementById('ob-overlay');
  var pill = document.getElementById('ob-pill');
  var titleEl = document.getElementById('ob-title');
  var textEl = document.getElementById('ob-text');
  var imgEl = document.getElementById('ob-img');
  var dotsEl = document.getElementById('ob-dots');
  var nextLabel = document.getElementById('ob-pill-label');
  var idx = 0;

  function render() {
    var step = STEPS[idx];
    if (!step || !overlay) return;
    titleEl.textContent = step.title;
    textEl.textContent = step.text;
    var src = media(step);
    imgEl.src = '';
    imgEl.src = src;
    imgEl.alt = step.title;
    dotsEl.querySelectorAll('.ob-step-dot').forEach(function (btn, i) {
      btn.classList.toggle('is-on', i === idx);
      btn.classList.toggle('is-done', !!(cfg.done && cfg.done[STEPS[i].key]));
    });
    var demoBtn = document.getElementById('ob-demo');
    if (demoBtn && step.href) {
      var url = step.href;
      if (step.onboard) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'onboard=' + step.onboard;
      demoBtn.setAttribute('href', url);
    }
  }

  function openAt(i) {
    idx = Math.max(0, Math.min(STEPS.length - 1, i));
    overlay.hidden = false;
    overlay.setAttribute('aria-hidden', 'false');
    if (pill) pill.hidden = true;
    render();
  }

  function close(opts) {
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    var st = load();
    st.seenWelcome = true;
    if (opts && opts.dismiss) st.dismissed = true;
    save(st);
    if (pill && !st.dismissed && !cfg.veteran) {
      pill.hidden = false;
      if (nextLabel) {
        var labels = {
          login: 'Log in',
          upload: 'Upload a FITS',
          target: 'Set your target',
          inspect: 'Inspect the frame',
          photometry: 'Run photometry',
          results: 'Read the magnitude',
          subtraction: 'Subtraction',
          done: 'Guide',
        };
        nextLabel.textContent = 'Next: ' + (labels[cfg.next] || 'Guide');
      }
    }
  }

  function maybeAutoOpen() {
    var st = load();
    var params = new URLSearchParams(location.search);
    if (params.get('onboard') === 'open' || params.get('guide') === '1') {
      openAt(0);
      return;
    }
    if (cfg.veteran || st.dismissed) {
      return;
    }
    if (!st.seenWelcome) {
      openAt(0);
      return;
    }
    if (pill) pill.hidden = false;
  }

  function spotlight() {
    var params = new URLSearchParams(location.search);
    var step = params.get('onboard');
    if (!step || step === 'open') return;
    var map = {
      upload: '[data-onboard="upload"]',
      inspect: '[data-onboard="inspect"]',
      photometry: '[data-onboard="photometry"]',
      subtraction: '[data-onboard="subtraction"]',
      transients: '[data-onboard="transients"]',
      target: '#id_target, [data-onboard="target"]',
    };
    var el = document.querySelector(map[step] || '[data-onboard="' + step + '"]');
    if (!el) return;
    el.classList.add('onboard-spotlight');
    el.scrollIntoView({ block: 'center', behavior: reduce ? 'auto' : 'smooth' });
    params.delete('onboard');
    var qs = params.toString();
    history.replaceState({}, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
  }

  document.querySelectorAll('[data-ob-goto]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openAt(parseInt(btn.getAttribute('data-ob-goto'), 10));
    });
  });
  var prev = document.getElementById('ob-prev');
  var next = document.getElementById('ob-next');
  if (prev) prev.addEventListener('click', function () { openAt(idx - 1); });
  if (next) next.addEventListener('click', function () { openAt(idx + 1); });
  var skip = document.getElementById('ob-skip');
  if (skip) skip.addEventListener('click', function () { close({ dismiss: true }); });
  var closeBtn = document.getElementById('ob-close');
  if (closeBtn) closeBtn.addEventListener('click', function () { close({}); });
  if (overlay) overlay.addEventListener('click', function (ev) {
    if (ev.target === overlay) close({});
  });
  document.addEventListener('keydown', function (ev) {
    if (overlay && !overlay.hidden && ev.key === 'Escape') close({});
  });
  if (pill) pill.addEventListener('click', function () { openAt(cfg.next === 'done' ? 0 : Math.max(0, STEPS.findIndex(function (s) { return s.key === cfg.next; }))); });
  var reopen = document.getElementById('onboard-reopen');
  if (reopen) reopen.addEventListener('click', function (ev) {
    ev.preventDefault();
    var st = load();
    st.dismissed = false;
    save(st);
    openAt(0);
  });

  STEPS.forEach(function (step, i) {
    if (!dotsEl) return;
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'ob-step-dot';
    b.textContent = i === 0 ? 'Intro' : String(i);
    b.addEventListener('click', function () { openAt(i); });
    dotsEl.appendChild(b);
  });

  maybeAutoOpen();
  spotlight();
})();
