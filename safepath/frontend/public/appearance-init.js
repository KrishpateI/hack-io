(function () {
  var d = document.documentElement;
  var s = 'system';
  try {
    var v = window.localStorage.getItem('safepath.appearance');
    if (v === 'light' || v === 'dark') s = v;
  } catch {
    /* storage unavailable: keep System */
  }
  var dark = true;
  try {
    if (typeof window.matchMedia === 'function') dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch {
    /* matchMedia unavailable: treat as dark */
  }
  var a = s === 'system' ? (dark ? 'dark' : 'light') : s;
  d.setAttribute('data-appearance', a);
  d.style.colorScheme = a;
  var m = document.querySelector('meta[name="theme-color"]');
  if (m) m.setAttribute('content', a === 'light' ? '#f2f2f7' : '#0b1220');
})();
