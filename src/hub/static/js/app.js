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
      // After Generate email: the error (autofocus) or the new draft, announced; else the panel.
      var generated = event.detail.requestConfig && event.detail.requestConfig.elt &&
        event.detail.requestConfig.elt.hasAttribute && event.detail.requestConfig.elt.hasAttribute("data-generate");
      var error = panel.querySelector(".email-area .form-error");
      var result = panel.querySelector("#email-result");
      if (generated && error) {
        error.focus();
        announce("");
      } else if (generated && result) {
        result.focus();
        announce("The draft email is ready below.");
      } else {
        panel.focus();
      }
    }
  });

  // One polite live region for long actions (Generate email), rendered once in base.html so that a change
  // is announced; it is empty when nothing is running (the visual indicator is hidden from everyone).
  function announce(text) {
    var region = document.getElementById("live-status");
    if (region) {
      region.textContent = text;
    }
  }

  // Generate email (HTMX): keep focus on the button, mark it busy instead of disabling it (a disabled
  // focused button drops focus to the page), and say what is happening. hx-sync drops a second click.
  document.addEventListener("htmx:beforeRequest", function (event) {
    var form = event.detail.elt;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-generate")) {
      return;
    }
    form.dataset.busy = "true";  // shows the indicator (CSS); never copied onto the swapped-in panel
    var button = form.querySelector("button[type=submit]");
    if (button) {
      button.setAttribute("aria-disabled", "true");
      button.dataset.label = button.textContent;
      button.textContent = button.dataset.busyText || button.textContent;
    }
    var note = form.querySelector(".generating");
    announce(note ? note.textContent : "Generating…");
  });
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail.elt;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-generate")) {
      return;
    }
    delete form.dataset.busy;
    var button = form.querySelector("button[type=submit]");
    if (button && document.contains(button)) {  // only if the form was not replaced
      button.removeAttribute("aria-disabled");
      button.textContent = button.dataset.label || button.textContent;
    }
  });
  document.addEventListener("click", function (event) {
    var busy = event.target.closest("[aria-disabled=true]");
    if (busy) {
      event.preventDefault();
    }
  }, true);

  // "Save and generate email" (a normal POST that can take up to the Claude timeout): show and announce
  // the working state, and refuse a second submit while it runs.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.id !== "professor-form") {
      return;
    }
    if (form.dataset.busy === "true") {
      event.preventDefault();
      return;
    }
    var submitter = event.submitter;
    if (submitter && submitter.name === "then" && submitter.value === "generate") {
      form.dataset.busy = "true";
      submitter.setAttribute("aria-disabled", "true");
      var note = form.querySelector(".generating-note");
      if (note) {
        note.textContent = note.dataset.generatingText;
      }
    }
  });

  // Copy buttons on the request email (US-10): data-copy-target names the element whose text is copied
  // (subject, body); data-copy-text is copied as is ("Refine in Claude Code" command). The result is
  // announced in the nearest .copy-status live region. Nothing is sent anywhere: the clipboard only.
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy-target], [data-copy-text]");
    if (!button) {
      return;
    }
    var text = button.dataset.copyText;
    if (text === undefined) {
      var source = document.getElementById(button.dataset.copyTarget);
      text = source ? source.textContent : "";
    }
    var status = button.parentElement.querySelector(".copy-status");
    var label = button.textContent.replace(/^Copy /, "").trim();
    function say(message) {
      if (status) {
        status.textContent = message;
      }
    }
    if (!navigator.clipboard) {
      say("Copying is not available here; select the text and copy it.");
      return;
    }
    navigator.clipboard.writeText(text).then(
      function () { say(label === "Refine in Claude Code" ? "Command copied." : "Copied " + label + "."); },
      function () { say("Copying failed; select the text and copy it."); }
    );
  });
})();
