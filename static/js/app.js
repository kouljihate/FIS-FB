/* Irrigation Planning System - Bootstrap 5 UI helpers.
 *
 * The markup is plain Bootstrap; this file only adds the behaviour that cannot
 * be expressed with data attributes alone: prefilling modals from the row that
 * opened them, the merge/swap selection toolbars and the canvas charts.
 */
(function () {
  "use strict";

  function modalFor(id) {
    var el = document.getElementById(id);
    if (!el || !window.bootstrap || !window.bootstrap.Modal) return null;
    return window.bootstrap.Modal.getOrCreateInstance(el);
  }

  /** Open a Bootstrap modal by element id. */
  window.openModal = function (id) {
    var modal = modalFor(id);
    if (modal) modal.show();
  };

  /** Close a Bootstrap modal by element id. */
  window.closeModal = function (id) {
    var modal = modalFor(id);
    if (modal) modal.hide();
  };

  // Names kept from the previous <dialog> implementation.
  window.openDialog = window.openModal;
  window.closeDialog = window.closeModal;

  /** Show or hide the global loading overlay. */
  window.setLoading = function (on) {
    var el = document.getElementById("globalLoading");
    if (el) el.classList.toggle("is-visible", !!on);
  };

  function selectedIds() {
    return Array.prototype.slice
      .call(document.querySelectorAll(".row-select:checked"))
      .map(function (box) { return box.value; });
  }

  function selectedNames() {
    return selectedIds().map(function (id) {
      var box = document.querySelector('.row-select[value="' + id + '"]');
      var row = box ? box.closest("tr") : null;
      if (!row || !row.cells[1]) return id;
      return row.cells[1].textContent.trim();
    });
  }

  function refreshSelectionState() {
    var ids = selectedIds();
    var counter = document.getElementById("selection-count");
    if (counter) counter.textContent = ids.length;

    document.querySelectorAll("[data-needs-two]").forEach(function (el) {
      el.disabled = ids.length !== 2;
    });

    var names = selectedNames();
    document.querySelectorAll("[data-selection-list]").forEach(function (el) {
      el.innerHTML = names.length
        ? "<strong>" + names.join(" &rarr; ") + "</strong>"
        : "<span class=\"text-secondary\">nothing selected yet · لم يتم اختيار شيء بعد</span>";
    });
  }

  /** Point the target modal's form at this row and fill its `data-fill` inputs. */
  function prepareModal(button) {
    var selector = button.getAttribute("data-bs-target");
    if (!selector || selector.charAt(0) !== "#") return;
    var modal = document.querySelector(selector);
    if (!modal) return;

    var form = modal.querySelector("form");
    var action = button.getAttribute("data-action");
    if (form && action) form.action = action;

    modal.querySelectorAll("[data-fill]").forEach(function (field) {
      var key = field.getAttribute("data-fill");
      var value = button.getAttribute("data-" + key);
      if (value === null) value = "";
      field.value = value;
    });

    var nameField = modal.querySelector('input[name="name"]');
    if (nameField) nameField.value = button.getAttribute("data-name") || "";
  }

  /** Submit-time plumbing: merge/swap carry the checked rows as hidden inputs. */
  function prepareSelectionForm(form, event) {
    form.querySelectorAll('input[data-selection-input]').forEach(function (n) { n.remove(); });
    var ids = selectedIds();
    if (ids.length !== 2) {
      event.preventDefault();
      return;
    }
    ids.forEach(function (id) {
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = "item_ids";
      input.setAttribute("data-selection-input", "1");
      input.value = id;
      form.appendChild(input);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Flash messages become auto-dismissing toasts.
    document.querySelectorAll(".flash-toast").forEach(function (el) {
      if (!window.bootstrap || !window.bootstrap.Toast) return;
      window.bootstrap.Toast.getOrCreateInstance(el, { delay: 7000, autohide: true }).show();
    });

    // Confirm anything destructive before it is sent.
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
      });
    });

    // Row buttons that open a modal pre-filled with that row's values.
    document.querySelectorAll("[data-action]").forEach(function (button) {
      button.addEventListener("click", function () { prepareModal(button); });
    });

    document.querySelectorAll("[data-modal]").forEach(function (button) {
      button.addEventListener("click", function () {
        window.openModal(button.getAttribute("data-modal"));
      });
    });

    // Checkbox selection drives the merge / swap toolbars.
    var checkboxes = document.querySelectorAll(".row-select");
    checkboxes.forEach(function (box) {
      box.addEventListener("change", refreshSelectionState);
    });
    if (checkboxes.length) refreshSelectionState();

    document.querySelectorAll("form[data-needs-two-form]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        prepareSelectionForm(form, event);
      });
    });

    // Long POSTs (sector / row / tree generation) get the overlay.
    document.querySelectorAll("form[data-loading]").forEach(function (form) {
      form.addEventListener("submit", function () { window.setLoading(true); });
    });

    // Charts declared on the page.
    document.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
      try {
        drawBarChart(canvas);
      } catch (err) {
        console.warn("chart failed", err);
      }
    });
  });

  function themeColor(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name);
    value = (value || "").trim();
    return value || fallback;
  }

  /**
   * Minimal grouped bar chart on a 2D canvas.
   * Expects data-labels and data-values as JSON arrays.
   */
  function drawBarChart(canvas) {
    var labels = JSON.parse(canvas.getAttribute("data-labels") || "[]");
    var values = JSON.parse(canvas.getAttribute("data-values") || "[]");
    if (!labels.length) return;

    var primary = themeColor("--bs-primary", "#eb6864");
    var bodyColor = themeColor("--bs-body-color", "#343a40");

    var dpr = window.devicePixelRatio || 1;
    var cssWidth = canvas.clientWidth || 600;
    var cssHeight = canvas.clientHeight || 260;
    canvas.width = cssWidth * dpr;
    canvas.height = cssHeight * dpr;

    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    var pad = { top: 16, right: 12, bottom: 48, left: 52 };
    var plotW = cssWidth - pad.left - pad.right;
    var plotH = cssHeight - pad.top - pad.bottom;
    if (plotW <= 0 || plotH <= 0) return;

    var max = Math.max.apply(null, values.concat([0.000001]));
    var barW = plotW / values.length;

    ctx.strokeStyle = themeColor("--bs-border-color", "#dee2e6");
    ctx.fillStyle = themeColor("--bs-secondary-color", "#6c757d");
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";

    for (var step = 0; step <= 4; step++) {
      var value = max * step / 4;
      var y = pad.top + plotH - (plotH * step / 4);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();
      ctx.fillText(formatNumber(value), 4, y);
    }

    values.forEach(function (value, index) {
      var h = max > 0 ? (value / max) * plotH : 0;
      var x = pad.left + index * barW + barW * 0.18;
      var w = barW * 0.64;
      var y = pad.top + plotH - h;

      ctx.fillStyle = primary;
      ctx.fillRect(x, y, w, h);

      ctx.fillStyle = bodyColor;
      ctx.save();
      ctx.translate(x + w / 2, pad.top + plotH + 14);
      ctx.rotate(-Math.PI / 8);
      ctx.textAlign = "start";
      var label = String(labels[index] || "");
      ctx.fillText(label.length > 14 ? label.slice(0, 13) + "…" : label, 0, 0);
      ctx.restore();
    });
  }

  function formatNumber(value) {
    if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString();
    if (Math.abs(value) >= 10) return Math.round(value);
    return Math.round(value * 100) / 100;
  }
})();
