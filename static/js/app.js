// Confirm dangerous forms; auto-submit filter forms when a select/date changes.
document.querySelectorAll('form[data-confirm]').forEach(function (f) {
  f.addEventListener('submit', function (e) {
    if (!window.confirm(f.dataset.confirm)) e.preventDefault();
  });
});
document.querySelectorAll('form[data-autosubmit]').forEach(function (f) {
  f.querySelectorAll('select, input[type=date]').forEach(function (el) {
    el.addEventListener('change', function () { f.submit(); });
  });
});
