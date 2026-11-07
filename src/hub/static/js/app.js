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

  // Add professor: course suggestions (US-21). "Use" fills course, description, grade and term and links the
  // course (a ticked checkbox under Linked courses); everything stays editable and nothing is saved until Save.
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-use-course]");
    if (!button) {
      return;
    }
    var fill = {
      "f-course": button.dataset.courseLabel,
      "f-course_description": button.dataset.courseDescription,
      "f-grade": button.dataset.courseGrade,
      "f-term": button.dataset.courseTerm
    };
    Object.keys(fill).forEach(function (id) {
      var field = document.getElementById(id);
      if (field) {
        field.value = fill[id] || "";
      }
    });
    var box = document.getElementById("linked-courses");
    var id = "course-" + button.dataset.courseId;
    if (box && !document.getElementById(id)) {
      var row = document.createElement("div");
      row.className = "field-check";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.id = id;
      input.name = "course_ids";
      input.value = button.dataset.courseId;
      input.checked = true;
      var label = document.createElement("label");
      label.htmlFor = id;
      label.textContent = button.dataset.courseLabel;
      row.appendChild(input);
      row.appendChild(label);
      box.appendChild(row);
      box.hidden = false;
    }
    announce("Linked " + button.dataset.courseLabel + "; course, description, grade and term filled in. Nothing is saved until Save.");
  });
  // The title follows the name's title token (Dr., Prof.) until the user edits the title themselves.
  document.addEventListener("input", function (event) {
    if (event.target.id === "f-title") {
      event.target.dataset.touched = "true";
    }
  });
  var lastSuggestions = "";
  document.addEventListener("htmx:afterSwap", function (event) {
    if (!event.target || event.target.id !== "course-suggestions") {
      return;
    }
    var count = event.target.querySelectorAll("[data-use-course]").length;
    var said = count ? count + " course" + (count === 1 ? "" : "s") + " suggested below the name." : "";
    if (said && said !== lastSuggestions) {
      announce(said);
    }
    lastSuggestions = said;
    var inner = event.target.querySelector("[data-title]");
    var title = document.getElementById("f-title");
    if (inner && title && inner.dataset.title && title.dataset.touched !== "true" && !document.querySelector("[data-existing-professor]")) {
      title.value = inner.dataset.title;
    }
  });

  // Long AI calls (research, re-research, later course import; DECISIONS #63). A data-working form is a
  // normal POST that can take minutes. While it runs:
  // - its button shows a spinner and the busy styling and is aria-disabled (focus stays on it);
  // - every other control on the page that could change data is aria-disabled and does nothing;
  // - its notice (data-notice) is shown with an elapsed timer; only the start is announced, never a tick.
  // The finish or failure is announced by the next page (data-announce) and focus moves to data-focus.
  var LOCKABLE = "button, input:not([type=hidden]), select, textarea, a[data-writes]";
  var working = null;  // {form, timer}

  function formatElapsed(seconds) {
    var minutes = Math.floor(seconds / 60);
    return minutes ? minutes + " min " + (seconds % 60) + " s" : seconds + " s";
  }

  function lockPage(form) {
    // the working form's own fields too: what is typed now would not be sent (ui review, #65)
    document.querySelectorAll("main " + LOCKABLE.split(", ").join(", main ")).forEach(function (el) {
      if (el.closest(".header-row") || el.getAttribute("aria-disabled") === "true") {
        return;
      }
      el.setAttribute("aria-disabled", "true");
      el.setAttribute("data-locked", "");
    });
  }

  function resetNotice(notice) {
    notice.classList.remove("working-failed");
    notice.querySelectorAll(".working-error").forEach(function (line) {
      line.remove();
    });
    var title = notice.querySelector(".working-title strong");
    if (title && notice.dataset.title) {
      title.textContent = notice.dataset.title;
    }
  }

  function unlockPage() {
    document.querySelectorAll("[data-locked]").forEach(function (el) {
      el.removeAttribute("aria-disabled");
      el.removeAttribute("data-locked");
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (working && form !== working.form && !form.closest(".header-row")) {
      event.preventDefault();  // another write while the call runs
      return;
    }
    if (!form.hasAttribute || !form.hasAttribute("data-working")) {
      return;
    }
    if (form.dataset.busy === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.busy = "true";
    var button = event.submitter || form.querySelector("button[type=submit]");
    if (button) {
      button.setAttribute("aria-disabled", "true");
      button.classList.add("is-working");
      button.dataset.label = button.textContent;
      button.textContent = button.dataset.busyText || button.textContent;
    }
    lockPage(form);
    var notice = document.getElementById(form.dataset.notice || "");
    var started = Date.now();
    var timer = null;
    if (notice) {
      resetNotice(notice);  // a retry after a failure starts clean (ui review round 2)
      notice.hidden = false;
      var clock = notice.querySelector(".working-timer");
      if (clock) {
        clock.textContent = formatElapsed(0);
        timer = window.setInterval(function () {
          clock.textContent = formatElapsed(Math.floor((Date.now() - started) / 1000));
        }, 1000);
      }
      announce(notice.dataset.start || "Working…");
    }
    working = {form: form, timer: timer};
  });

  // Locked controls do nothing: no click, no key that changes a value, no HTMX request.
  ["click", "mousedown", "keydown", "change", "beforeinput", "paste", "drop"].forEach(function (type) {
    document.addEventListener(type, function (event) {
      var locked = event.target.closest && event.target.closest("[data-locked]");
      if (!locked) {
        return;
      }
      if (type === "keydown" && (event.key === "Tab" || event.key === "Escape")) {
        return;  // keyboard users can still move past it
      }
      event.preventDefault();
      event.stopPropagation();
    }, true);
  });
  document.addEventListener("htmx:confirm", function (event) {
    if (working && !event.target.closest(".header-row")) {
      event.preventDefault();
    }
  });

  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) {
      return;
    }
    if (working && working.timer) {
      window.clearInterval(working.timer);
    }
    working = null;
    unlockPage();
    document.querySelectorAll("form[data-working]").forEach(function (form) {
      delete form.dataset.busy;
      form.querySelectorAll("[aria-disabled=true]").forEach(function (button) {
        button.removeAttribute("aria-disabled");
        button.classList.remove("is-working");
        button.textContent = button.dataset.label || button.textContent;
      });
    });
    document.querySelectorAll(".working-notice").forEach(function (notice) {
      resetNotice(notice);
      notice.hidden = true;
    });
  });

  // A data-working form runs through HTMX (hx-post) so the page stays while it works. Whatever comes back
  // (the result, or a 409 page with the error) replaces the page's main column, never the error toast.
  document.addEventListener("htmx:beforeSwap", function (event) {
    var elt = event.detail.elt || event.detail.requestConfig && event.detail.requestConfig.elt;
    if (!elt || !elt.hasAttribute || !elt.hasAttribute("data-working")) {
      return;
    }
    var xhr = event.detail.xhr;
    if (!xhr || String(xhr.responseText || "").indexOf("class=\"main-inner") === -1) {
      // not one of the app's pages (e.g. a plain-text 500): never blank the page (ui review, #65)
      event.detail.shouldSwap = false;
      failWorking("The app answered with an error (HTTP " + (xhr ? xhr.status : "?") + "). Nothing was saved. Try again.");
      return;
    }
    event.detail.shouldSwap = true;
    event.detail.isError = false;
    event.detail.target = document.querySelector(".main-inner");
    event.detail.swapOverride = "outerHTML";
    event.detail.selectOverride = ".main-inner";
  });
  // htmx:load fires on the new main column (the form that started the call was swapped out with it, so
  // events on that form never reach the document).
  document.addEventListener("htmx:load", function (event) {
    if (!working || !event.target.classList || !event.target.classList.contains("main-inner")) {
      return;
    }
    if (working.timer) {
      window.clearInterval(working.timer);
    }
    working = null;
    arrive();
  });
  // A long call that ends without one of the app's pages: stop the spinner and the timer, show the error in
  // the notice (visible, and announced once), unlock the page, give the button back (ui review, #65).
  function failWorking(message) {
    if (!working) {
      return;
    }
    var form = working.form;
    if (working.timer) {
      window.clearInterval(working.timer);
    }
    working = null;
    unlockPage();
    delete form.dataset.busy;
    form.querySelectorAll("[aria-disabled=true]").forEach(function (button) {
      button.removeAttribute("aria-disabled");
      button.classList.remove("is-working");
      button.textContent = button.dataset.label || button.textContent;
    });
    var notice = document.getElementById(form.dataset.notice || "");
    if (notice) {
      notice.classList.add("working-failed");
      var title = notice.querySelector(".working-title strong");
      if (title) {
        title.textContent = "Stopped.";
      }
      var line = notice.querySelector(".working-error");
      if (!line) {
        line = document.createElement("p");
        line.className = "working-error";
        line.tabIndex = -1;
        notice.appendChild(line);
      }
      line.textContent = message;
      line.focus();  // focus goes to the error when the call ends (docs/03 section 5)
    }
    announce(message);
  }
  document.addEventListener("htmx:sendError", function (event) {
    if (working && event.detail.elt === working.form) {
      failWorking("The app could not be reached (is the Application Hub window still open?). Nothing was saved. Try again.");
    }
  });
  document.addEventListener("htmx:responseError", function (event) {
    if (working && event.detail.elt === working.form) {
      failWorking("The app answered with an error (HTTP " + event.detail.xhr.status + "). Nothing was saved. Try again.");
    }
  });

  // The page that ends a long call says how it ended and puts focus on its result (DECISIONS #63).
  document.addEventListener("DOMContentLoaded", arrive);
  function arrive() {
    var main = document.querySelector(".main-inner[data-focus], .main-inner[data-announce]");
    if (!main) {
      return;
    }
    var target = document.getElementById(main.dataset.focus || "");
    if (target) {
      if (!target.hasAttribute("tabindex")) {
        target.setAttribute("tabindex", "-1");
      }
      target.focus();
    }
    if (main.dataset.announce) {
      window.setTimeout(function () { announce(main.dataset.announce); }, 50);
    }
  }

  // Back to the Add/Edit page from the browser's back-forward cache: the page comes back as it was left,
  // still busy, which would silently block every later submit. Reset the busy state.
  window.addEventListener("pageshow", function (event) {
    var form = document.getElementById("professor-form");
    if (!event.persisted || !form) {
      return;
    }
    delete form.dataset.busy;
    form.querySelectorAll("[aria-disabled=true]").forEach(function (button) {
      button.removeAttribute("aria-disabled");
    });
    var note = form.querySelector(".generating-note");
    if (note) {
      note.textContent = "";
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
