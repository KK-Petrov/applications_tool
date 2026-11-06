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
})();
