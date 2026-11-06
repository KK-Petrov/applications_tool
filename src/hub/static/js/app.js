// Application Hub: small behaviours on top of htmx (docs/03 §5). No framework, no external requests.
//
// - A failed HTMX action that ends in the page-level toast (#action-error) resets the toggle or menu
//   form it came from (data-reset-on-error), so it shows the stored value again; data-entry forms
//   keep what was typed.
// - The toast clears after the next successful action, and can be dismissed.
(function () {
  "use strict";

  function toast() {
    return document.getElementById("action-error");
  }

  document.addEventListener("htmx:afterRequest", function (event) {
    var xhr = event.detail.xhr;
    var status = xhr ? xhr.status : 0;
    var box = toast();
    if (status >= 400) {
      var form = event.detail.elt && event.detail.elt.closest ? event.detail.elt.closest("form") : null;
      // only toggles and menus (data-reset-on-error); a data-entry form keeps what was typed
      if (form && form.hasAttribute("data-reset-on-error")) {
        form.reset(); // back to the values the server rendered, i.e. what is stored
      }
    } else if (status >= 200 && status < 300 && box && event.detail.target !== box) {
      box.innerHTML = "";
    }
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-dismiss-toast]") : null;
    var box = toast();
    if (button && box) {
      box.innerHTML = "";
    }
  });

  // Recommender sheet (docs/03 §4.3) ------------------------------------------------------------
  // - Filter and Sort apply on change (the Apply button stays for keyboard users and without JS).
  // - One status menu open at a time; Escape or a click elsewhere closes it and returns focus.
  // - The highlighted row follows the detail panel after an HTMX swap.

  document.addEventListener("change", function (event) {
    var select = event.target;
    if (select && select.hasAttribute && select.hasAttribute("data-autosubmit") && select.form) {
      select.form.requestSubmit();
    }
  });

  function openMenus() {
    return document.querySelectorAll("details.status-menu[open]");
  }

  document.addEventListener("toggle", function (event) {
    var menu = event.target;
    if (!menu.classList || !menu.classList.contains("status-menu") || !menu.open) {
      return;
    }
    openMenus().forEach(function (other) {
      if (other !== menu) {
        other.open = false;
      }
    });
  }, true);

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    openMenus().forEach(function (menu) {
      menu.open = false;
      var summary = menu.querySelector("summary");
      if (summary) {
        summary.focus();
      }
    });
  });

  document.addEventListener("click", function (event) {
    var inside = event.target.closest ? event.target.closest("details.status-menu") : null;
    openMenus().forEach(function (menu) {
      if (menu !== inside) {
        menu.open = false;
      }
    });
  });

  // The selected professor is whoever the panel shows now (a click swaps the panel without a reload),
  // so every sheet request sends that, never the value rendered into the page.
  function selectedId() {
    var panel = document.getElementById("panel");
    return panel ? panel.getAttribute("data-professor") || "" : "";
  }

  document.addEventListener("htmx:configRequest", function (event) {
    var elt = event.detail.elt;
    if (elt && elt.closest && elt.closest("#sheet")) {
      event.detail.parameters.selected = selectedId();
    }
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches("#sheet-controls")) {
      return;
    }
    var id = selectedId();
    var input = form.querySelector("input[name=selected]");
    if (!id) {
      if (input) {
        input.remove();
      }
      return;
    }
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "selected";
      form.appendChild(input);
    }
    input.value = id;
  }, true);

  document.addEventListener("htmx:afterSwap", function (event) {
    var panel = document.getElementById("panel");
    if (!panel) {
      return;
    }
    var fromPanel = event.detail.target === panel || event.detail.elt === panel || panel.contains(event.detail.elt);
    var id = panel.getAttribute("data-professor");
    document.querySelectorAll("#sheet tbody tr[id^='row-']").forEach(function (row) {
      var on = row.id === "row-" + id;
      row.classList.toggle("selected", on);
      if (on) {
        row.setAttribute("aria-current", "true");
      } else {
        row.removeAttribute("aria-current");
      }
    });
    if (fromPanel) {
      panel.focus();
    }
  });
})();
