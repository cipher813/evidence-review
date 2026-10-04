"use strict";
const token =
  new URLSearchParams(location.hash.slice(1)).get("token") ||
  sessionStorage.getItem("review-token");
if (token) sessionStorage.setItem("review-token", token);
history.replaceState(null, "", location.pathname);
let bundle,
  state,
  answers,
  queue = Promise.resolve(),
  selection = null,
  subject = null,
  active = 0,
  conflicted = false,
  last = performance.now(),
  lastInteraction = performance.now();
const $ = (id) => document.getElementById(id);
function el(tag, text) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  return n;
}
async function api(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json();
  if (!r.ok) {
    const error = Error(d.error || r.status);
    error.status = r.status;
    throw error;
  }
  return d;
}
function status() {
  const completed = Object.keys(answers.judgments).length;
  const hook = state.hook;
  $("status").className = "";
  $("status").textContent =
    `Saved locally · revision ${state.revision} · ${completed}/${bundle.form.length} fields answered · continuation ${hook.status}: ${hook.reason || ""}`;
  $("next").disabled =
    state.last_submission !== state.revision || hook.status !== "succeeded";
}
function account() {
  const current = performance.now();
  if (
    document.visibilityState === "visible" &&
    document.hasFocus() &&
    current - lastInteraction < 60000
  )
    active += (current - last) / 1000;
  last = current;
}
for (const event of ["pointerdown", "keydown", "input"])
  document.addEventListener(event, () => {
    account();
    lastInteraction = performance.now();
  });
setInterval(account, 1000);
function save(submit = false) {
  const snapshot = structuredClone(answers);
  const elapsed = active;
  active = 0;
  const reason = $("amendment").value;
  const key = crypto.randomUUID();
  $("next").disabled = true;
  queue = queue
    .catch(() => {})
    .then(async () => {
      if (conflicted) {
        $("status").textContent =
          "Revision conflict: reload saved state before editing.";
        return;
      }
      $("status").textContent = "Saving…";
      const body = {
        bundle_id: bundle.bundle_id,
        bundle_hash: state.bundle_hash,
        revision: state.revision,
        key,
        answers: snapshot,
        assessor: $("assessor").value.trim(),
        active_seconds: Math.min(elapsed, 3600),
      };
      if (submit) body.amendment_reason = reason;
      try {
        state = await api(submit ? "/api/submit" : "/api/save", body);
        status();
      } catch (err) {
        if (err.status === 409) conflicted = true;
        if (!err.status || err.status >= 500) {
          try {
            const restored = await api("/api/state");
            if (restored.operations[key]) {
              state = restored;
              status();
              return;
            }
          } catch (_) {
            /* explicit unsaved status below */
          }
        }
        active += elapsed;
        $("status").className = "save-error";
        $("status").textContent =
          "Not saved: " +
          err.message +
          (conflicted
            ? " · reload to reconcile before continuing."
            : " · correct the fields and save again.");
        $("next").disabled = true;
      }
    });
  return queue;
}
function judgment(id) {
  return (
    answers.judgments[id] ||
    (answers.judgments[id] = {
      value: "",
      note: "",
      claim_ids: [],
      selections: [],
    })
  );
}
function showSource(source, start = 1, end = start) {
  const box = $("evidence");
  const title = el("h3", source.title);
  box.append(title);
  box.append(
    el("p", "Source metadata: " + JSON.stringify(source.metadata || {})),
  );
  box.append(el("small", "Frozen SHA-256: " + source.sha256));
  const lines = source.text.split("\n");
  const wrap = el("div");
  let anchor = null;
  lines.forEach((text, i) => {
    if (i === lines.length - 1 && text === "") return;
    const n = el("button", `L${i + 1}: ${text}`);
    n.type = "button";
    n.className = "source-line";
    if (i + 1 >= start && i + 1 <= end) n.classList.add("selected");
    n.onclick = () => {
      if (anchor === null) anchor = i + 1;
      else {
        const lo = Math.min(anchor, i + 1),
          hi = Math.max(anchor, i + 1);
        selection = {
          source_id: source.source_id,
          source_hash: source.sha256,
          start_line: lo,
          end_line: hi,
          excerpt: lines.slice(lo - 1, hi).join("\n"),
          subject_id: subject,
        };
        anchor = null;
        for (const [j, line] of [...wrap.children].entries())
          line.classList.toggle("selected", j + 1 >= lo && j + 1 <= hi);
        $("status").textContent =
          `Selected L${lo}–L${hi}. Attach to a judgment or defect.`;
      }
    };
    wrap.append(n);
  });
  box.append(
    el(
      "small",
      "Click the first and last line to select a passage (same line twice for one line). Full source includes headers and footnotes.",
    ),
  );
  box.append(wrap);
  const target = wrap.children[start - 1];
  if (target) target.scrollIntoView({ block: "nearest" });
}
function cite(c) {
  const box = $("evidence");
  box.append(
    el(
      "p",
      `${c.source_id} · ${c.start_line === null ? "unresolved" : `L${c.start_line}–L${c.end_line}`} · ${c.status}${c.reason ? ": " + c.reason : ""}`,
    ),
  );
  if (c.excerpt) box.append(el("blockquote", c.excerpt));
  const source = bundle.sources.find((s) => s.source_id === c.source_id);
  if (source) {
    const btn = el("button", "Open full frozen source");
    btn.onclick = () =>
      showSource(source, c.start_line || 1, c.end_line || c.start_line || 1);
    box.append(btn);
  }
}
function showSubject(item, id) {
  subject = id;
  selection = null;
  $("evidence").replaceChildren(el("h3", item.text));
  (item.citations || []).forEach(cite);
  if (item.calculation) {
    const c = item.calculation;
    $("evidence").append(
      el("p", `Formula: ${c.formula} = ${c.result} ${c.unit}`),
      el("p", `Absolute tolerance: ${c.tolerance} ${c.unit}`),
    );
    c.operands.forEach((o) => {
      $("evidence").append(
        el(
          "p",
          `Input ${o.name}: ${o.value} ${o.unit} · period ${o.period || "unavailable"}`,
        ),
      );
      if (o.citation) cite(o.citation);
      else $("evidence").append(el("p", "Input source unavailable."));
    });
    (c.conversions || []).forEach((v) =>
      $("evidence").append(el("p", "Conversion: " + v)),
    );
    $("evidence").append(
      el(
        "p",
        "Recomputation: " +
          JSON.stringify(c.recomputation || { status: "unresolved" }),
      ),
    );
  }
  if (!(item.citations || []).length && !item.calculation)
    $("evidence").append(
      el("p", "No cited evidence; search the frozen sources."),
    );
}
function showSpan(span) {
  const linked = span.claim_ids
    .map((id) => bundle.claims.find((c) => c.claim_id === id))
    .filter(Boolean);
  if (linked.length === 1) showSubject(linked[0], linked[0].claim_id);
  else {
    subject = null;
    $("evidence").replaceChildren(
      el("h3", `${span.text}: ${span.state}`),
      el(
        "p",
        span.reason ||
          "No unique evidence mapping. Search frozen sources; no support is implied.",
      ),
    );
    linked.forEach((c) => {
      const b = el("button", c.text);
      b.onclick = () => showSubject(c, c.claim_id);
      $("evidence").append(b);
    });
  }
}
function renderReport() {
  const report = $("report");
  report.replaceChildren();
  bundle.fields.forEach((f) => {
    report.append(el("h3", f.label));
    const p = el("div");
    p.className = "report-text";
    let offset = 0;
    const spans = bundle.spans
      .filter((s) => s.field_path === f.path)
      .sort((a, b) => a.start - b.start);
    const chars = Array.from(f.text);
    spans.forEach((s) => {
      p.append(document.createTextNode(chars.slice(offset, s.start).join("")));
      const b = el("button", s.text);
      b.className =
        "number " +
        (["uncited", "ambiguous", "unavailable"].includes(s.state)
          ? "unresolved"
          : "");
      b.title = s.state;
      b.onclick = () => showSpan(s);
      p.append(b);
      offset = s.end;
    });
    p.append(document.createTextNode(chars.slice(offset).join("")));
    report.append(p);
  });
}
function renderForms() {
  $("forms").replaceChildren();
  bundle.form.forEach((f) => {
    const div = el("div");
    div.className = "field";
    let control;
    if (f.kind === "boolean" && !f.require_true) {
      control = el("select");
      control.append(
        new Option("Choose…", ""),
        new Option("Yes", "true"),
        new Option("No", "false"),
      );
      const value = answers.judgments[f.field_id]?.value;
      control.value = typeof value === "boolean" ? String(value) : "";
    } else if (f.kind === "choice") {
      control = el("select");
      control.append(new Option("Choose…", ""));
      f.options.forEach((v) => control.append(new Option(v, v)));
      control.value = answers.judgments[f.field_id]?.value || "";
    } else {
      control = el("input");
      control.type = f.kind === "boolean" ? "checkbox" : "text";
      if (f.kind === "boolean")
        control.checked = answers.judgments[f.field_id]?.value === true;
      else control.value = answers.judgments[f.field_id]?.value || "";
    }
    const label = el("label", f.label + (f.required ? " *" : ""));
    control.id = "field-" + f.field_id;
    control.setAttribute("aria-label", f.label);
    label.append(control);
    div.append(label);
    control[f.kind === "text" ? "oninput" : "onchange"] = () => {
      const value =
        f.kind === "boolean"
          ? f.require_true
            ? control.checked
            : control.value === ""
              ? ""
              : control.value === "true"
          : control.value;
      if (value === "") delete answers.judgments[f.field_id];
      else judgment(f.field_id).value = value;
      save();
    };
    const note = el("textarea");
    note.setAttribute("aria-label", "Explanation: " + f.label);
    note.placeholder =
      "Explanation (required for uncertainty/defects where specified)";
    note.value = answers.judgments[f.field_id]?.note || "";
    note.oninput = () => {
      judgment(f.field_id).note = note.value;
      save();
    };
    div.append(note);
    const links = el("div");
    bundle.claims.forEach((c) => {
      const l = el("label");
      const check = el("input");
      check.type = "checkbox";
      check.checked = (answers.judgments[f.field_id]?.claim_ids || []).includes(
        c.claim_id,
      );
      check.onchange = () => {
        const j = judgment(f.field_id);
        j.claim_ids = check.checked
          ? [...new Set([...j.claim_ids, c.claim_id])]
          : j.claim_ids.filter((id) => id !== c.claim_id);
        save();
      };
      l.append(check, document.createTextNode(" Link claim " + c.claim_id));
      links.append(l);
    });
    div.append(links);
    const attach = el("button", "Attach selected source passage");
    attach.onclick = () => {
      if (!selection) return;
      judgment(f.field_id).selections.push(structuredClone(selection));
      renderForms();
      save();
    };
    div.append(attach);
    for (const [i, s] of (
      answers.judgments[f.field_id]?.selections || []
    ).entries()) {
      const line = el(
        "p",
        `${s.source_id} L${s.start_line}–L${s.end_line}: ${s.excerpt}`,
      );
      const remove = el("button", "Remove passage");
      remove.onclick = () => {
        judgment(f.field_id).selections.splice(i, 1);
        renderForms();
        save();
      };
      line.append(remove);
      div.append(line);
    }
    $("forms").append(div);
  });
}
function renderDefects() {
  $("defects").replaceChildren();
  answers.defects.forEach((d, i) => {
    const div = el("div");
    div.className = "defect";
    div.append(el("h4", "Defect " + (i + 1)));
    const category = el("input");
    category.setAttribute("aria-label", "Defect category");
    category.value = d.category;
    category.oninput = () => {
      d.category = category.value;
      save();
    };
    div.append(category);
    const material = el("select");
    material.setAttribute("aria-label", "Defect materiality");
    material.append(
      new Option("Choose materiality…", ""),
      new Option("Nonmaterial", "false"),
      new Option("Material", "true"),
    );
    material.value = d.material === null ? "" : String(d.material);
    material.onchange = () => {
      d.material = material.value === "" ? null : material.value === "true";
      save();
    };
    div.append(material);
    const note = el("textarea");
    note.setAttribute("aria-label", "Defect evidence explanation");
    note.value = d.evidence_note;
    note.oninput = () => {
      d.evidence_note = note.value;
      save();
    };
    div.append(note);
    for (const [items, key, label] of [
      [bundle.claims, "claim_ids", "claim_id"],
      [bundle.references, "reference_ids", "reference_id"],
    ])
      items.forEach((item) => {
        const l = el("label");
        const c = el("input");
        c.type = "checkbox";
        c.checked = d[key].includes(item[label]);
        c.onchange = () => {
          d[key] = c.checked
            ? [...new Set([...d[key], item[label]])]
            : d[key].filter((id) => id !== item[label]);
          save();
        };
        l.append(c, document.createTextNode(" " + item[label]));
        div.append(l);
      });
    const attach = el("button", "Attach passage to defect");
    attach.onclick = () => {
      if (selection) {
        d.selections.push(structuredClone(selection));
        renderDefects();
        save();
      }
    };
    div.append(attach);
    d.selections.forEach((s) =>
      div.append(
        el("p", `${s.source_id} L${s.start_line}–L${s.end_line}: ${s.excerpt}`),
      ),
    );
    const remove = el("button", "Remove defect");
    remove.onclick = () => {
      answers.defects.splice(i, 1);
      renderDefects();
      save();
    };
    div.append(remove);
    $("defects").append(div);
  });
}
function render() {
  $("task").textContent = bundle.bundle_id + " · " + bundle.task_kind;
  renderReport();
  renderForms();
  renderDefects();
  $("subjects").replaceChildren();
  [...bundle.claims, ...bundle.references].forEach((c) => {
    const b = el("button", c.text);
    b.onclick = () => showSubject(c, c.claim_id || c.reference_id);
    $("subjects").append(b);
  });
  $("sources").replaceChildren();
  bundle.sources.forEach((s) => {
    const b = el("button", s.title);
    b.onclick = () => {
      $("evidence").replaceChildren();
      showSource(s);
    };
    $("sources").append(b);
  });
  Object.entries(bundle.disclosures || {}).forEach(([label, text]) =>
    $("subjects").append(el("h3", label), el("pre", text)),
  );
  status();
}
async function load() {
  bundle = await api("/api/bundle");
  state = await api("/api/state");
  answers = structuredClone(state.answers);
  $("assessor").value =
    state.assessor ||
    state.suggested_assessor ||
    sessionStorage.getItem("assessor") ||
    "";
  queue = Promise.resolve();
  render();
}
$("assessor").oninput = () =>
  sessionStorage.setItem("assessor", $("assessor").value.trim());
$("add-defect").onclick = () => {
  answers.defects.push({
    defect_id: "D" + crypto.randomUUID(),
    category: "",
    material: null,
    claim_ids:
      subject && bundle.claims.some((c) => c.claim_id === subject)
        ? [subject]
        : [],
    reference_ids: [],
    evidence_note: "",
    selections: selection ? [structuredClone(selection)] : [],
  });
  renderDefects();
  save();
};
$("submit").onclick = () => save(true).catch(() => {});
$("reconcile").onclick = () => {
  queue = queue
    .then(async () => {
      state = await api("/api/reconcile", {
        bundle_id: bundle.bundle_id,
        bundle_hash: state.bundle_hash,
      });
      status();
    })
    .catch(
      (err) =>
        ($("status").textContent = "Continuation unresolved: " + err.message),
    );
};
$("next").onclick = async () => {
  await queue;
  const result = await api("/api/next", {
    bundle_id: bundle.bundle_id,
    bundle_hash: state.bundle_hash,
  });
  if (result.done) {
    $("status").textContent = "Queue complete. No pending tasks.";
    return;
  }
  $("amendment").value = "";
  selection = null;
  subject = null;
  $("evidence").replaceChildren();
  await load();
};
$("search").oninput = () => {
  const term = $("search").value.toLowerCase();
  $("search-results").replaceChildren();
  if (!term) return;
  bundle.sources.forEach((s) =>
    s.text.split("\n").forEach((line, i) => {
      if (line.toLowerCase().includes(term)) {
        const b = el("button", `${s.title} L${i + 1}: ${line}`);
        b.onclick = () => {
          $("evidence").replaceChildren();
          showSource(s, i + 1, i + 1);
        };
        $("search-results").append(b);
      }
    }),
  );
};
window.addEventListener("beforeunload", (event) => {
  if (
    $("status").textContent.startsWith("Saving") ||
    $("status").className === "save-error"
  ) {
    event.preventDefault();
    event.returnValue = "";
  }
});
load().catch((err) => {
  $("status").className = "save-error";
  $("status").textContent = err.message;
});
