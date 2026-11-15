// DDX-ES progressive enhancement. Every page works without this file; it only
//  - swaps one region in place for links and GET forms marked data-swap="#id" (tabs, pickers, filters),
//  - submits selects and checkboxes marked data-autosubmit, and search boxes after a pause in typing,
//  - answers consultation questions from the keyboard (Y, N, 0-9),
//  - keeps "A"/"An" right in the case-presentation sentence, and fills the SPARQL editor from the examples.
(() => {
  "use strict";

  async function swap(url, selector, { push = true } = {}) {
    const target = document.querySelector(selector);
    if (!target) { location.assign(url); return; }
    target.setAttribute("aria-busy", "true");
    try {
      const res = await fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
      if (!res.ok) throw new Error(res.status);
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const fresh = doc.querySelector(selector);
      if (!fresh) throw new Error("missing " + selector);
      target.replaceWith(fresh);
      document.title = doc.title;
      if (push) history.pushState({ swap: selector }, "", url);
    } catch (err) {
      location.assign(url);
    }
  }

  function formUrl(form) {
    const url = new URL(form.getAttribute("action") || location.pathname, location.href);
    url.search = new URLSearchParams(new FormData(form)).toString();
    if (form.dataset.hash) url.hash = form.dataset.hash;
    return url.toString();
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-swap]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    swap(a.href, a.dataset.swap);
  });

  document.addEventListener("submit", (e) => {
    const form = e.target.closest("form[data-swap]");
    if (!form || (form.method || "get").toLowerCase() !== "get") return;
    e.preventDefault();
    swap(formUrl(form), form.dataset.swap);
  });

  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.matches("[data-autosubmit]") && el.form) el.form.requestSubmit();
  });

  let typing;
  document.addEventListener("input", (e) => {
    const el = e.target;
    if (!el.matches("[data-autosubmit-typing]") || !el.form) return;
    clearTimeout(typing);
    typing = setTimeout(() => {
      const form = el.form, pos = el.selectionStart, id = el.id;
      form.requestSubmit();
      // keep the caret in the search box after the region is swapped
      setTimeout(() => { const box = document.getElementById(id); if (box) { box.focus(); box.setSelectionRange(pos, pos); } }, 350);
    }, 300);
  });

  window.addEventListener("popstate", () => location.reload());

  // Keyboard answers: Y / N for yes-no questions, digits for a 0-10 scale.
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, select, textarea, [contenteditable]")) return;
    const key = e.key.toLowerCase();
    const btn = document.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (btn) { e.preventDefault(); btn.click(); }
  });

  // "A 45-year-old" / "An 8-year-old"
  const age = document.getElementById("age");
  const article = document.querySelector("[data-article]");
  if (age && article) {
    const update = () => {
      const v = age.value.trim();
      article.textContent = v.startsWith("8") || v === "11" || v === "18" ? "An" : "A";
    };
    age.addEventListener("input", update);
    update();
  }

  // Long answer lists (body locations): a filter box and a running count of the ticked options.
  document.querySelectorAll("[data-filter]").forEach((box) => {
    const set = box.closest("fieldset");
    const items = [...set.querySelectorAll(".choice")];
    const count = set.closest("form").querySelector("[data-count]");
    box.hidden = false;
    box.addEventListener("input", () => {
      const q = box.value.trim().toLowerCase();
      items.forEach((it) => { it.hidden = q !== "" && !it.textContent.toLowerCase().includes(q) && !it.querySelector("input").checked; });
    });
    set.addEventListener("change", () => {
      const n = set.querySelectorAll("input:checked").length;
      if (count) count.textContent = n ? `${n} selected` : "";
    });
  });

  // A select inside a sentence is as wide as its chosen option, not its longest one.
  document.querySelectorAll("select.inline").forEach((sel) => {
    const probe = document.createElement("span");
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre;left:-9999px";
    sel.after(probe);
    const fit = () => {
      probe.style.font = getComputedStyle(sel).font;
      probe.textContent = sel.options[sel.selectedIndex]?.text || "";
      sel.style.width = Math.ceil(probe.getBoundingClientRect().width + parseFloat(getComputedStyle(sel).fontSize) * 1.25) + "px";
    };
    sel.addEventListener("change", fit);
    if (document.fonts) document.fonts.ready.then(fit);
    fit();
  });

  // SPARQL: examples fill the editor; Ctrl+Enter runs the query.
  const editor = document.getElementById("query");
  if (editor) {
    document.querySelectorAll("[data-example]").forEach((a) => {
      a.addEventListener("click", (e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey) return;
        e.preventDefault();
        editor.value = a.dataset.query;
        const key = document.querySelector("[data-example-key]");
        if (key) key.value = a.dataset.example;
        const hint = /--with-patient\s+(\w+)/.exec(a.dataset.query.split("\n")[0]);
        const patient = editor.form.querySelector('[name="patient"]');
        if (patient) patient.value = hint ? hint[1] : "";
        document.querySelectorAll("[data-example]").forEach((x) => x.removeAttribute("aria-current"));
        a.setAttribute("aria-current", "true");
        history.replaceState(null, "", "/sparql?example=" + encodeURIComponent(a.dataset.example));
        editor.focus();
      });
    });
    editor.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); editor.form.requestSubmit(); }
    });
  }
})();
