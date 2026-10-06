"use strict";
const token =
  new URLSearchParams(location.hash.slice(1)).get("token") ||
  sessionStorage.getItem("review-token");
if (token) sessionStorage.setItem("review-token", token);
history.replaceState(null, "", location.pathname);
let bundle,
  evidence = { views: {}, inventory: null },
  state,
  answers,
  queue = Promise.resolve(),
  selection = null,
  subject = null,
  current = null,
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
// Text fragment (#:~:text=) so the original opens at the cited words where the
// browser supports it; otherwise it opens at the top and the excerpt is shown here.
function fragmentFor(excerpt) {
  const words = (excerpt || "")
    .split("\n")
    .map((line) => line.replace(/[|*#>_`]/g, " ").replace(/\s+/g, " ").trim())
    .find((line) => line.split(" ").length >= 3);
  if (!words) return "";
  const enc = (t) => encodeURIComponent(t).replace(/-/g, "%2D").replace(/,/g, "%2C").replace(/&/g, "%26");
  return "#:~:text=" + enc(words.split(" ").slice(0, 8).join(" "));
}
function originalHref(sourceId, excerpt, start, end) {
  const link = (evidence.links || {})[sourceId];
  if (!link) return null;
  return start && link.line_url
    ? link.line_url.replaceAll("{start}", String(start)).replaceAll("{end}", String(end || start))
    : link.url + fragmentFor(excerpt);
}
// A number bound to exactly one located source line opens that line directly.
function directCitation(span) {
  if (span.state !== "cited" || span.calculation || span.citations.length !== 1) return null;
  const c = span.citations[0];
  if (c.status !== "located" || !(evidence.links || {})[c.source_id]?.line_url) return null;
  const href = originalHref(c.source_id, c.excerpt, c.start_line, c.end_line);
  return href ? { citation: c, href } : null;
}
function originalLink(sourceId, excerpt, start, end) {
  const link = (evidence.links || {})[sourceId];
  if (!link) {
    const none = el(
      "p",
      "No public original is recorded for this source; only the frozen copy can be checked.",
    );
    none.className = "no-original";
    return none;
  }
  const wrap = el("p");
  const lines = start ? (end && end !== start ? `L${start}–L${end}` : `L${start}`) : "";
  const a = el(
    "a",
    (link.label || "Open the original document") + (lines && link.line_url ? `, ${lines}` : ""),
  );
  a.href = originalHref(sourceId, excerpt, start, end);
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.className = "original-link";
  a.onclick = () => logEvent("original_opened", sourceId);
  wrap.append(a);
  if (link.note) wrap.append(el("small", " " + link.note));
  return wrap;
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
    `Draft: Saved locally · revision ${state.revision} · ${completed}/${bundle.form.length} fields answered`;
  // Consumer backup/import status is never folded into the local save message.
  const submitted = state.last_submission
    ? `submitted revision ${state.last_submission}`
    : "not submitted";
  $("continuation").className = "hook-" + hook.status;
  const delivery = { succeeded: "delivery verified", failed: "delivery failed", pending: "delivery pending", unknown: "delivery unknown: reconcile" }[hook.status] || hook.status;
  $("continuation").textContent = state.last_submission
    ? `Continuation ${hook.status} (${delivery}) · ${submitted}` +
      (hook.identifier ? ` · receipt ${hook.identifier}` : "") +
      (hook.reason ? ` · ${hook.reason}` : "")
    : "Continuation not started · nothing submitted yet";
  $("next").disabled =
    state.last_submission !== state.revision || hook.status !== "succeeded";
}
function logEvent(kind, subject = "") {
  // Navigation is recorded as exposure only; it never marks evidence verified.
  api("/api/event", {
    bundle_id: bundle.bundle_id,
    bundle_hash: state.bundle_hash,
    kind,
    subject: String(subject).slice(0, 200),
  }).catch(() => {});
}
function differences(mine, saved) {
  const out = [];
  const ids = new Set([
    ...Object.keys(mine.judgments),
    ...Object.keys(saved.judgments),
  ]);
  for (const id of ids)
    if (
      JSON.stringify(mine.judgments[id] || null) !==
      JSON.stringify(saved.judgments[id] || null)
    ) {
      const field = bundle.form.find((f) => f.field_id === id);
      out.push(field ? field.label : id);
    }
  if (JSON.stringify(mine.defects) !== JSON.stringify(saved.defects))
    out.push("Defects");
  return out;
}
async function showConflict(message) {
  $("conflict").hidden = false;
  $("conflict-message").textContent =
    "Not saved: " +
    message +
    ". Another tab or process saved a newer revision. Nothing is overwritten until you choose.";
  $("conflict-fields").replaceChildren();
  let latest = null;
  try {
    latest = await api("/api/state");
  } catch (_) {
    /* the bundle itself may have changed */
  }
  const sameBundle = latest && latest.bundle_hash === state.bundle_hash;
  $("keep-mine").hidden = !sameBundle;
  if (!sameBundle) {
    $("conflict-message").textContent =
      "Not saved: the review task changed on disk. Load the saved version to continue.";
    return;
  }
  const changed = differences(answers, latest.answers);
  if (!changed.length)
    $("conflict-fields").append(el("li", "No answer differs from the saved revision."));
  changed.forEach((label) =>
    $("conflict-fields").append(el("li", "Differs from saved: " + label)),
  );
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
        if (err.status === 409) {
          conflicted = true;
          showConflict(err.message);
        }
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
  logEvent("source_opened", source.source_id);
  const box = $("evidence");
  const title = el("h3", source.title);
  box.append(title);
  box.append(
    el("p", "Source metadata: " + JSON.stringify(source.metadata || {})),
  );
  box.append(el("small", "Frozen SHA-256: " + source.sha256));
  const lines = source.text.split("\n");
  box.append(originalLink(source.source_id, lines.slice(start - 1, end).join("\n"), start, end));
  const wrap = el("div");
  let anchor = null;
  lines.forEach((text, i) => {
    if (i === lines.length - 1 && text === "") return;
    const n = el("button", `L${i + 1}: ${text}`);
    n.type = "button";
    n.className = "source-line";
    const picked = i + 1 >= start && i + 1 <= end;
    n.classList.toggle("selected", picked);
    n.setAttribute("aria-pressed", String(picked));
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
        for (const [j, line] of [...wrap.children].entries()) {
          const on = j + 1 >= lo && j + 1 <= hi;
          line.classList.toggle("selected", on);
          line.setAttribute("aria-pressed", String(on));
        }
        logEvent("passage_selected", `${source.source_id}:${lo}:${hi}`);
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
function cite(c, box = $("evidence")) {
  box.append(
    el(
      "p",
      `${c.source_id} · ${c.start_line === null ? "unresolved" : `L${c.start_line}–L${c.end_line}`} · ${c.status}${c.reason ? ": " + c.reason : ""}`,
    ),
  );
  const view = evidence.views[`${c.source_id}:${c.start_line}:${c.end_line}`];
  if (view?.table?.status === "parsed") {
    const t = view.table;
    t.context.forEach((r) => box.append(el("p", r.text)));
    const wrap = el("div");
    wrap.className = "table-scroll";
    const table = el("table");
    table.append(el("caption", "Frozen table context · cited rows highlighted"));
    const head = el("thead"), hr = el("tr");
    t.headers.forEach((h) => { const cell = el("th", h); cell.scope = "col"; hr.append(cell); });
    head.append(hr); table.append(head);
    const body = el("tbody");
    const matches = t.rows.flatMap((r) => r.cells.map((value, i) => ({r, i, value})))
      .filter((x) => x.r.cited && x.value === c.excerpt.trim());
    t.rows.forEach((r) => {
      const row = el("tr");
      row.className = r.cited ? "cited" : "";
      row.title = `Frozen source line L${r.line}`;
      r.cells.forEach((value, i) => {
        const cell = el(i === 0 ? "th" : "td", value);
        if (i === 0) cell.scope = "row";
        if (matches.length === 1 && matches[0].r === r && matches[0].i === i) cell.className = "cited-cell";
        row.append(cell);
      });
      body.append(row);
    });
    table.append(body); wrap.append(table); box.append(wrap);
    t.notes.forEach((r) => box.append(el("p", `L${r.line} (note): ${r.text}`)));
  } else if (view) {
    if (view.table?.reason !== "not a table") box.append(el("p", "Table structure could not be resolved"));
    else box.append(el("blockquote", view.text));
  } else if (c.excerpt) box.append(el("blockquote", c.excerpt));
  if (view) {
    const raw = el("details");
    raw.append(el("summary", "Original numbered lines (raw fallback)"));
    const ctx = el("div"); ctx.className = "context";
    view.lines.forEach((row) => {
      const label = {cited: "cited", table_header: "table header", note: "note"}[row.role];
      const line = el("div", `L${row.line}${label ? " (" + label + ")" : ""}: ${row.text}`);
      line.className = "ctx " + row.role; ctx.append(line);
    });
    raw.append(ctx); box.append(raw);
  }
  if (c.status !== "located") box.append(el("p", "No source located"));
  else if (!(evidence.links || {})[c.source_id]?.line_url) box.append(el("p", "No exact source link; use the frozen preview."));
  if (c.start_line !== null)
    box.append(originalLink(c.source_id, c.excerpt, c.start_line, c.end_line));
  const source = bundle.sources.find((s) => s.source_id === c.source_id);
  if (source) {
    box.append(el("small", `${source.title} · Frozen SHA-256: ${source.sha256}`));
    const btn = el("button", "Open full frozen source");
    btn.onclick = () =>
      showSource(source, c.start_line || 1, c.end_line || c.start_line || 1);
    box.append(btn);
  }
}
function showSubject(item, id, follow = true) {
  subject = id;
  if (id && follow) followSubject(id);
  $("evidence-panel").open = true;
  selection = null;
  logEvent("subject_opened", id || "");
  $("evidence").replaceChildren(el("h3", item.text));
  (item.citations || []).forEach((c) => cite(c));
  if (item.calculation) renderCalculation(item.calculation, $("evidence"), true);
  if (!(item.citations || []).length && !item.calculation)
    $("evidence").append(
      el("p", "No cited evidence; search the frozen sources."),
    );
}
function operandValue(o, box, preview) {
  const row = el("p");
  row.append(document.createTextNode(`Input ${o.name}: `));
  const c = o.citation;
  const href = c?.status === "located" && (evidence.links || {})[c.source_id]?.line_url
    ? originalHref(c.source_id, c.excerpt, c.start_line, c.end_line) : null;
  const value = el(href ? "a" : "span", o.value);
  if (href) { value.href = href; value.target = "_blank"; value.rel = "noopener noreferrer";
    value.onclick = () => logEvent("original_opened", c.source_id); }
  row.append(value, document.createTextNode(` ${o.unit || "(unit unavailable)"} · period ${o.period || "unavailable"}${o.entity ? " · " + o.entity : ""}`));
  box.append(row);
  if (o.kind === "constant") box.append(el("p", "Mathematical constant: " + o.value));
  else if (o.calculation) renderCalculation(o.calculation, box, preview);
  else if (!c || c.status !== "located") {
    box.append(el("p", `No source located for input ${o.name}`));
    if (c) box.append(el("p", `${c.status}: ${c.reason}`));
  }
  else if (preview) cite(c, box);
  else {
    const p = el("button", "Preview input " + o.name);
    p.onclick = () => { $("evidence-panel").open = true; $("evidence").replaceChildren(); cite(c); };
    box.append(p);
  }
}
function renderCalculation(c, box, preview = false) {
  const r = c.recomputation || { status: "unresolved" };
  box.append(el("h4", "Calculation"), el("p", `Formula: ${c.formula}`),
    el("p", `Reported result: ${c.result} ${c.unit}`), el("p", `Absolute tolerance: ${c.tolerance} ${c.unit}`));
  c.operands.forEach((o) => operandValue(o, box, preview));
  // Literal constants are mathematical factors, never financial source claims.
  const namesRemoved = c.formula.replace(/[A-Za-z_]\w*/g, "");
  const constants = [...new Set(namesRemoved.match(/\d+(?:\.\d+)?/g) || [])];
  constants.forEach((v) => box.append(el("p", "Mathematical constant: " + v)));
  (c.conversions || []).forEach((v) => box.append(el("p", "Conversion: " + v)));
  box.append(el("p", r.status === "unresolved" ? `Arithmetic unresolved: ${r.reason || "cannot recompute"}`
    : `Recomputed (Decimal): ${r.result} · arithmetic ${r.status} · discrepancy ${r.discrepancy}`));
  if (r.evidence) {
    box.append(el("p", r.evidence.status === "all_operands_cited"
      ? "Input evidence: every input has a located citation."
      : `Input evidence unresolved: no located citation for ${r.evidence.missing.join(", ")}.`));
    r.evidence.warnings.forEach((w) => box.append(el("p", "Warning: " + w)));
    box.append(el("p", r.evidence.note));
  }
  box.append(el("p", "Arithmetic agreement does not verify input selection or interpretation."));
}
function showCalculation(span, anchor) {
  document.querySelectorAll(".calculation-card").forEach((n) => n.remove());
  const card = el("aside"); card.className = "calculation-card";
  card.setAttribute("role", "region"); card.setAttribute("aria-label", "Calculation details");
  renderCalculation(span.calculation, card);
  const preview = el("button", "Preview calculation evidence");
  preview.onclick = () => showSpan(span, false);
  const close = el("button", "Close calculation details"); close.onclick = () => { card.remove(); anchor.focus(); };
  card.append(preview, close); anchor.after(card);
  logEvent("span_opened", span.span_id);
}
function numberAction(s) {
  const direct = directCitation(s);
  const b = el(direct ? "a" : "button", s.text);
  if (direct) { b.href = direct.href; b.target = "_blank"; b.rel = "noopener noreferrer"; }
  b.className = "number " + (["uncited", "ambiguous", "unavailable"].includes(s.state) ? "unresolved"
    : s.state === "identifier" ? "identifier" : direct ? "direct" : "");
  b.title = s.state + (s.reason ? ": " + s.reason : "");
  b.setAttribute("aria-label", `${s.text}, ${s.state}${direct ? ", opens the cited line of the original" : ""}`);
  b.onclick = () => {
    if (direct) logEvent("original_opened", direct.citation.source_id);
    else if (s.calculation) showCalculation(s, b);
    else showSpan(s, false);
  };
  const fragment = document.createDocumentFragment(); fragment.append(b);
  if (direct) {
    const preview = el("button", "Preview evidence"); preview.className = "preview-link";
    preview.setAttribute("aria-label", "Preview evidence for " + s.text);
    preview.onclick = () => showSpan(s, false); fragment.append(preview);
  }
  return fragment;
}
function linkedStatement(text, subjectId) {
  const wrap = el("blockquote"); wrap.className = "item-text";
  const field = bundle.fields.find((f) => f.role !== "context" && f.text === text && f.claim_ids.includes(subjectId));
  if (!field) { wrap.textContent = text; return wrap; }
  let at = 0; const chars = Array.from(text);
  bundle.spans.filter((s) => s.field_path === field.path).sort((a,b) => a.start-b.start).forEach((s) => {
    wrap.append(document.createTextNode(chars.slice(at,s.start).join("")), numberAction(s)); at = s.end;
  });
  wrap.append(document.createTextNode(chars.slice(at).join(""))); return wrap;
}
function showSpan(span, follow = true) {
  $("evidence-panel").open = true;
  logEvent("span_opened", span.span_id);
  const contextLine = () => {
    if (!span.context) return;
    const c = span.context;
    $("evidence").prepend(el("p", `Preparation context (${c.status}): metric ${c.metric || "unavailable"} · entity ${c.entity || "unavailable"} · period ${c.period || "unavailable"} · unit ${c.unit || "unavailable"}`));
  };
  if (span.prepared_evidence?.length) {
    $("evidence").replaceChildren(el("h3", `${span.text}: ${span.state}`), el("p", span.reason), el("p", "Prepared for review; not supplied by the answer. Candidate evidence is preserved below."));
    contextLine();
    if (span.state === "ambiguous") $("evidence").append(el("p", "Multiple possible sources—no exact match established."));
    span.prepared_evidence.forEach((p) => {
      $("evidence").append(el("p", p.reason));
      p.citations.forEach((c) => cite(c));
      if (p.calculation) renderCalculation(p.calculation, $("evidence"), true);
    });
    $("evidence").append(el("h4", "Candidate-supplied evidence"));
    span.citations.forEach((c) => cite(c));
    if (span.calculation) renderCalculation(span.calculation, $("evidence"), true);
    if (!span.citations.length && !span.calculation) $("evidence").append(el("p", "No source located in the answer"));
    return;
  }
  const linked = span.claim_ids
    .map((id) => bundle.claims.find((c) => c.claim_id === id))
    .filter(Boolean);
  if (span.citations.length || span.calculation) {
    // Evidence bound to this number, then the claim it belongs to.
    const item = {
      text: `${span.text}: ${span.calculation ? "calculated" : span.state}${span.reason ? " · " + span.reason : ""}`,
      citations: span.citations,
      calculation: span.calculation,
    };
    const claim = linked.length === 1 ? linked[0] : null;
    showSubject(item, claim ? claim.claim_id : null, follow);
    if (span.context) {
      const ctx = span.context;
      $("evidence").prepend(el("p", `Preparation context (${ctx.status}): metric ${ctx.metric || "unavailable"} · entity ${ctx.entity || "unavailable"} · period ${ctx.period || "unavailable"} · unit ${ctx.unit || "unavailable"}`));
    }
    if (span.state === "ambiguous" || span.citations.length > 1) {
      $("evidence").prepend(el("p", span.state === "ambiguous"
        ? "Multiple possible sources—no exact match established." : "Choose a candidate-supplied source; no support is implied."));
      const chooser = el("fieldset"); chooser.append(el("legend", "Source choices"));
      span.citations.forEach((c, i) => {
        const href = c.status === "located" && (evidence.links || {})[c.source_id]?.line_url
          ? originalHref(c.source_id, c.excerpt, c.start_line, c.end_line) : null;
        const option = el(href ? "a" : "span", `Source ${i + 1}: ${c.source_id} ${c.start_line ? "L" + c.start_line + "–L" + c.end_line : "unresolved"}`);
        if (href) { option.href = href; option.target = "_blank"; option.rel = "noopener noreferrer";
          option.onclick = () => logEvent("original_opened", c.source_id); }
        chooser.append(option);
      });
      $("evidence").prepend(chooser);
    }
    if (claim) {
      const b = el("button", `Whole claim ${claim.claim_id}: ${claim.text}`);
      b.className = "claim-link";
      b.onclick = () => showSubject(claim, claim.claim_id, false);
      $("evidence").append(b);
    }
  } else {
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
      const b = el("button", "Whole claim: " + c.text);
      b.onclick = () => showSubject(c, c.claim_id, false);
      $("evidence").append(b);
    });
    contextLine();
    $("evidence").append(el("p", span.state === "ambiguous"
      ? "Multiple possible sources—no exact match established." : "No source located."));
    if (["uncited", "ambiguous", "unavailable"].includes(span.state)) {
      // Offer every frozen line containing the number; a match is a lead, not support.
      $("search").value = span.text;
      $("search").oninput();
      $("evidence").append(
        el("p", `Lines containing "${span.text}" are listed under the source search; a match does not imply support.`),
      );
    }
  }
}
function renderReport() {
  const report = $("report");
  report.replaceChildren();
  $("context").replaceChildren();
  const context = bundle.fields.filter((f) => f.role === "context");
  $("context-box").hidden = !context.length;
  context.forEach((f) => $("context").append(el("h3", f.label), el("p", f.text)));
  bundle.fields.filter((f) => f.role !== "context").forEach((f) => {
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
      p.append(numberAction(s));
      offset = s.end;
    });
    p.append(document.createTextNode(chars.slice(offset).join("")));
    report.append(p);
  });
}
function subjectOf(f) {
  return (
    bundle.claims.find((c) => c.claim_id === f.subject_id) ||
    bundle.references.find((r) => r.reference_id === f.subject_id) ||
    null
  );
}
function isReference(id) {
  return bundle.references.some((r) => r.reference_id === id);
}
function answered(f) {
  const j = answers.judgments[f.field_id];
  if (!j) return false;
  if (f.require_true && j.value !== true) return false;
  if (f.note_required_unless.length && !f.note_required_unless.includes(j.value) && !j.note.trim()) return false;
  if (f.evidence_required && !j.selections.length) return false;
  return f.kind === "text" ? String(j.value).trim() !== "" : j.value !== "";
}
function itemFields() {
  const ids = [...new Set(bundle.form.filter((f) => f.subject_id).map((f) => f.subject_id))];
  return ids.flatMap((id) => bundle.form.filter((f) => f.subject_id === id));
}
function short(text, n = 90) {
  return text.length > n ? text.slice(0, n - 1) + "…" : text;
}
// Items in order, starting after the current one, wrapping round.
function nextUnanswered() {
  const items = itemFields();
  const at = items.findIndex((f) => f.field_id === current);
  for (let k = 1; k <= items.length; k++) {
    const f = items[(at + k + items.length) % items.length];
    if (!answered(f)) return f;
  }
  return null;
}
// Opening a statement from the report also makes its decision the current item.
function followSubject(id) {
  const f = itemFields().find((x) => x.subject_id === id);
  if (f && f.field_id !== current) {
    current = f.field_id;
    renderForms();
  }
}
function goTo(fieldId, focus = true) {
  current = fieldId;
  const f = bundle.form.find((x) => x.field_id === fieldId);
  const item = f && subjectOf(f);
  if (item) showSubject(item, f.subject_id, false);
  renderForms();
  if (focus) $("field-" + fieldId)?.focus();
}
function claimLinks(f, div) {
  // Coverage may rest on several claims; show their text, not bare identifiers.
  const box = el("fieldset");
  box.append(el("legend", "Claims that address this item (choose all that apply)"));
  bundle.claims.forEach((c) => {
    const l = el("label");
    l.className = "claim-choice";
    const check = el("input");
    check.type = "checkbox";
    check.checked = (answers.judgments[f.field_id]?.claim_ids || []).includes(c.claim_id);
    check.onchange = () => {
      const j = judgment(f.field_id);
      j.claim_ids = check.checked
        ? [...new Set([...j.claim_ids, c.claim_id])]
        : j.claim_ids.filter((id) => id !== c.claim_id);
      save();
    };
    l.append(check, document.createTextNode(` ${c.claim_id}: ${short(c.text)}`));
    box.append(l);
  });
  div.append(box);
}
function fieldControl(f, div) {
  let control;
  if (f.kind === "boolean" && !f.require_true) {
    control = el("select");
    control.append(new Option("Choose…", ""), new Option("Yes", "true"), new Option("No", "false"));
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
    if (f.kind === "boolean") control.checked = answers.judgments[f.field_id]?.value === true;
    else control.value = answers.judgments[f.field_id]?.value || "";
  }
  if (f.help) div.append(el("p", f.help));
  const meanings = Object.entries(f.option_help || {});
  if (meanings.length) {
    const dl = el("dl");
    dl.className = "option-help";
    meanings.forEach(([option, text]) => dl.append(el("dt", option), el("dd", text)));
    div.append(dl);
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
    progress();
  };
  const note = el("textarea");
  note.setAttribute("aria-label", "Explanation: " + f.label);
  note.placeholder = f.note_required_unless.length
    ? `Explanation (required unless ${f.note_required_unless.join(" or ")})`
    : "Explanation (optional)";
  note.value = answers.judgments[f.field_id]?.note || "";
  note.oninput = () => {
    judgment(f.field_id).note = note.value;
    save();
    progress();
  };
  div.append(note);
  if (f.subject_id && isReference(f.subject_id)) claimLinks(f, div);
  const attach = el("button", "Attach selected source passage");
  attach.onclick = () => {
    if (!selection) {
      $("status").textContent = "Select a passage first: open a source and click its first and last line.";
      return;
    }
    judgment(f.field_id).selections.push(structuredClone(selection));
    renderForms();
    save();
  };
  div.append(attach);
  for (const [i, s] of (answers.judgments[f.field_id]?.selections || []).entries()) {
    const line = el("p", `${s.source_id} L${s.start_line}–L${s.end_line}: ${s.excerpt}`);
    const remove = el("button", "Remove passage");
    remove.onclick = () => {
      judgment(f.field_id).selections.splice(i, 1);
      renderForms();
      save();
    };
    line.append(remove);
    div.append(line);
  }
}
function progress() {
  const required = bundle.form.filter((f) => f.required);
  const done = required.filter(answered).length;
  const items = itemFields();
  const at = items.findIndex((f) => f.field_id === current);
  const next = nextUnanswered();
  const judgments = required.filter((f) => !(f.kind === "boolean" && f.require_true));
  const optional = bundle.form.filter((f) => !f.required).length;
  const technical = required.length - judgments.length;
  const jAt = judgments.findIndex((f) => f.field_id === current);
  $("progress").textContent =
    `Judgment ${jAt >= 0 ? jAt + 1 : 1} of ${judgments.length} in this answer · ` +
    `${done} of ${required.length} required fields complete · ${optional} optional field${optional === 1 ? "" : "s"} · ${technical} completion control${technical === 1 ? "" : "s"}` +
    (at >= 0 ? ` · item ${at + 1} of ${items.length}` : "") +
    (next ? ` · next unanswered: ${next.label}` : items.length ? " · every item answered; finish below" : "");
  const field = bundle.form.find((f) => f.field_id === current);
  const j = field && answers.judgments[field.field_id];
  const missing = [];
  if (j && field.note_required_unless.length && !field.note_required_unless.includes(j.value) && !j.note.trim()) missing.push("explanation");
  if (j && field.evidence_required && !j.selections.length) missing.push("source passage");
  if (missing.length) $("progress").textContent += " · Pending: " + missing.join(" and ");
  const button = $("next-item");
  if (button) button.textContent = next ? "Next unanswered item" : "Go to finish";
}
function renderForms() {
  const items = itemFields();
  if (current === null || !items.some((f) => f.field_id === current))
    current = (items.find((f) => !answered(f)) || items[0] || {}).field_id ?? null;
  const evidencePanel = $("evidence-panel"); evidencePanel.remove();
  $("items").replaceChildren();
  items.forEach((f, i) => {
    const item = subjectOf(f);
    const div = el("div");
    div.className = "item" + (answered(f) ? " answered" : "");
    if (f.field_id !== current) {
      const open = el(
        "button",
        `${i + 1}. ${f.label} · ${answered(f) ? "answered" : "unanswered"}${item ? " · " + short(item.text, 70) : ""}`,
      );
      open.className = "item-link";
      open.onclick = () => goTo(f.field_id);
      div.append(open);
      $("items").append(div);
      return;
    }
    div.classList.add("current");
    div.setAttribute("aria-label", "Current item");
    div.append(el("h3", `Item ${i + 1} of ${items.length}: ${f.label}`));
    if (item) {
      div.append(el("p", isReference(f.subject_id) ? "Reference item:" : "Statement under review:"));
      div.append(linkedStatement(item.text, f.subject_id));
      div.append(el("small", isReference(f.subject_id) ? "Coverage: decide whether the answer addresses this reference." : "Evidence support: decide whether the source supports the statement as written."));
    }
    div.append(evidencePanel);
    fieldControl(f, div);
    const nav = el("div");
    nav.className = "item-nav";
    const prev = el("button", "Previous item");
    prev.disabled = i === 0;
    prev.onclick = () => goTo(items[i - 1].field_id);
    const next = el("button", "Next unanswered item");
    next.id = "next-item";
    next.onclick = () => {
      const target = nextUnanswered();
      if (target) goTo(target.field_id);
      else $("field-" + (bundle.form.find((x) => !x.subject_id) || {}).field_id)?.focus();
    };
    nav.append(prev, next, el("small", " Keys: n next unanswered, p previous."));
    div.append(nav);
    $("items").append(div);
  });
  if (!items.length) $("items").append(evidencePanel);
  $("forms").replaceChildren();
  bundle.form
    .filter((f) => !f.subject_id)
    .forEach((f) => {
      const div = el("div");
      div.className = "field";
      fieldControl(f, div);
      $("forms").append(div);
    });
  progress();
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
        l.append(c, document.createTextNode(` ${item[label]}: ${short(item.text)}`));
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
  $("workload").replaceChildren();
  if (evidence.workload || bundle.workload) {
    const w = evidence.workload || bundle.workload;
    const label = ["independent", "adjudication"].includes(bundle.task_kind) ? "Answer" : "Task";
    $("workload").append(el("p", `${label} ${w.answer_index} of ${w.assigned_answers} assigned`),
      el("p", Object.entries(w.task_counts).map(([kind,n]) => `${kind}: ${n}`).join(" · ")),
      el("p", "Why assigned: " + w.assignment_reason));
    w.expansion_conditions.forEach((c) => $("workload").append(el("p", "Conditional future work: " + c)));
  } else $("workload").append(el("p", "Assigned answer total not supplied by caller"));
  $("instructions").replaceChildren();
  if (bundle.instructions) {
    $("instructions").append(el("h2", "What to do"));
    bundle.instructions.split("\n").filter(Boolean).forEach((t) => $("instructions").append(el("p", t)));
  }
  renderReport();
  const inv = evidence.inventory;
  if (inv) {
    const states = Object.entries(inv.spans_by_state)
      .map(([k, v]) => `${v} ${k}`)
      .join(", ");
    $("report").prepend(
      el(
        "p",
        `Numbers: ${inv.span_total}${states ? " (" + states + ")" : ""} · subjects with unresolved evidence: ${inv.unresolved_subjects.length ? inv.unresolved_subjects.join(", ") : "none"}`,
      ),
    );
  }
  renderForms();
  const first = bundle.form.find((f) => f.field_id === current);
  if (first && subjectOf(first)) showSubject(subjectOf(first), first.subject_id, false);
  $("evidence-panel").open = false;
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
  evidence = await api("/api/evidence");
  state = await api("/api/state");
  conflicted = false;
  $("conflict").hidden = true;
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
  current = null;
  $("evidence").replaceChildren();
  await load();
};
$("use-saved").onclick = () => {
  queue = queue.catch(() => {}).then(load);
};
$("keep-mine").onclick = () => {
  queue = queue
    .catch(() => {})
    .then(async () => {
      const latest = await api("/api/state");
      if (latest.bundle_hash !== state.bundle_hash) return load();
      // Explicit choice: this tab's answers become a new revision on top of the saved one.
      state = latest;
      conflicted = false;
      $("conflict").hidden = true;
    });
  return queue.then(() => save());
};
document.addEventListener("keydown", (event) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(
    document.activeElement?.tagName,
  );
  if (event.key === "/" && !typing) {
    event.preventDefault();
    $("evidence-panel").open = true;
    $("search").focus();
  } else if ((event.key === "n" || event.key === "p") && !typing && !event.metaKey && !event.ctrlKey) {
    const items = itemFields();
    const at = items.findIndex((f) => f.field_id === current);
    const target = event.key === "n" ? nextUnanswered() : items[at - 1];
    if (target) {
      event.preventDefault();
      goTo(target.field_id);
    }
  } else if (event.key === "Escape" && selection) {
    selection = null;
    $("status").textContent = "Passage selection cleared.";
  }
});
let searchTimer = null;
$("search").oninput = () => {
  const term = $("search").value.toLowerCase();
  $("search-results").replaceChildren();
  clearTimeout(searchTimer);
  if (!term) return;
  searchTimer = setTimeout(() => logEvent("search", ""), 800);
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
