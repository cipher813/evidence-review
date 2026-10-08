"use strict";
// The launch token is read from the fragment and the fragment is scrubbed before
// anything that can throw. Tab storage is optional: when the browser denies it,
// the token lives only in this page's memory and a reload shows safe recovery.
const launchFragment = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
const tabStore = (() => {
  const memory = new Map();
  let available = true;
  const storage = () => { try { return window.sessionStorage; } catch (error) { available = false; return null; } };
  return {
    get(key) {
      try { const v = storage()?.getItem(key); if (v !== null && v !== undefined) return v; }
      catch (error) { available = false; }
      return memory.has(key) ? memory.get(key) : null;
    },
    set(key, value) {
      memory.set(key, value);
      try { storage()?.setItem(key, value); } catch (error) { available = false; }
    },
    get available() { return available; },
  };
})();
const token = new URLSearchParams(launchFragment).get("token") || tabStore.get("review-token");
if (token) tabStore.set("review-token", token);
let bundle,
  evidence = { views: {}, inventory: null },
  state,
  answers,
  queue = Promise.resolve(),
  selection = null,
  answerSelection = null,
  subject = null,
  current = null,
  revealedFields = new Set(),
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
async function api(path, body, signal) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  const d = await r.json();
  if (!r.ok) {
    const error = Error(d.error || r.status);
    error.status = r.status;
    throw error;
  }
  return d;
}
function matchesSubmitted(candidate, assessor) {
  const prior = state.submissions?.[String(state.last_submission)];
  return prior && prior.complete && prior.bundle_hash === state.bundle_hash && prior.assessor === assessor
    && !differences(candidate, { judgments: prior.judgments, defects: prior.defects,
      annotations: prior.annotations, worksheets: prior.worksheets }).length;
}
function requiredFieldsOnly() {
  return evidence.presentation?.required_fields_only === true;
}
function presentationFields() {
  return bundle.form.filter((f) => !requiredFieldsOnly() || f.required || revealedFields.has(f.field_id));
}
function status() {
  const fields = requiredFieldsOnly() ? bundle.form.filter((f) => f.required) : bundle.form;
  const completed = fields.filter((f) => Object.hasOwn(answers.judgments, f.field_id)).length;
  const hook = state.hook;
  $("status").className = "";
  $("status").textContent =
    `${matchesSubmitted(answers, $("assessor").value.trim()) ? "Submitted" : "Draft"}: Saved locally · revision ${state.revision} · ${completed}/${fields.length} ${requiredFieldsOnly() ? "required " : ""}fields answered`;
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
    !matchesSubmitted(answers, $("assessor").value.trim()) || hook.status !== "succeeded";
  showWorksheetResults();
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
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}
function differences(mine, saved) {
  const out = [];
  const ids = new Set([
    ...Object.keys(mine.judgments),
    ...Object.keys(saved.judgments),
  ]);
  for (const id of ids)
    if (
      JSON.stringify(canonical(mine.judgments[id] || null)) !==
      JSON.stringify(canonical(saved.judgments[id] || null))
    ) {
      const field = bundle.form.find((f) => f.field_id === id);
      out.push(field ? field.label : id);
    }
  if (JSON.stringify(canonical(mine.defects)) !== JSON.stringify(canonical(saved.defects)))
    out.push("Defects");
  // Saved records omit an empty annotation list; absence and [] are the same answer.
  if (JSON.stringify(canonical(mine.annotations || [])) !== JSON.stringify(canonical(saved.annotations || [])))
    out.push("Answer annotations");
  if (JSON.stringify(worksheetInputs(mine)) !== JSON.stringify(worksheetInputs(saved)))
    out.push("Calculation worksheets");
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
function referenceEvidenceReceipts(f) {
  const j = answers.judgments[f.field_id];
  if (bundle.task_kind !== "reference" || !j || !f.subject_id || f.numeric_span_id ||
      f.kind !== "choice" || !f.options.includes(j.value)) return [];
  const quantities = bundle.form.filter((q) => q.numeric_span_id && q.subject_id === f.subject_id);
  const verifiedQuantities = (f.numeric_verification_values || []).includes(j.value);
  const explainedConcern = f.note_required_unless.length && !f.note_required_unless.includes(j.value);
  if (explainedConcern && !j.note.trim()) return [];
  if (verifiedQuantities && quantities.some((q) => answers.judgments[q.field_id]?.value !== true)) return [];
  if (quantities.length && !verifiedQuantities && !explainedConcern) return [];
  const receipts = new Map();
  const addCitation = (c) => {
    const source = bundle.sources.find((s) => s.source_id === c.source_id);
    if (!source || c.status !== "located" || !Number.isInteger(c.start_line) ||
        !Number.isInteger(c.end_line) || c.start_line < 1 || c.end_line < c.start_line) return false;
    const view = evidence.views[`${c.source_id}:${c.start_line}:${c.end_line}`];
    if (!view || typeof view.text !== "string") return false;
    const receipt = {source_id: source.source_id, source_hash: source.sha256,
      start_line: c.start_line, end_line: c.end_line,
      excerpt: view.text, subject_id: f.subject_id};
    receipts.set(JSON.stringify([receipt.source_id, receipt.start_line, receipt.end_line]), receipt);
    return true;
  };
  const addCalculation = (calculation) => {
    let located = false;
    for (const operand of calculation?.operands || []) {
      if (operand.citation) located = addCitation(operand.citation) || located;
      if (operand.calculation) located = addCalculation(operand.calculation) || located;
    }
    return located;
  };
  for (const q of quantities) {
    const span = bundle.spans.find((s) => s.span_id === q.numeric_span_id);
    const evidenceRows = span?.prepared_evidence?.length ? span.prepared_evidence : [span];
    let located = false;
    for (const row of evidenceRows) {
      for (const c of row?.citations || []) located = addCitation(c) || located;
      located = addCalculation(row?.calculation) || located;
    }
    // Evidence receipts are bookkeeping for explicit checks, never new verdicts.
    if (verifiedQuantities && !located) return [];
  }
  if (!quantities.length || explainedConcern) {
    const reference = bundle.references.find((r) => r.reference_id === f.subject_id);
    for (const citation of reference?.citations || []) addCitation(citation);
    addCalculation(reference?.calculation);
  }
  return [...receipts.values()];
}
function attachReferenceEvidenceReceipts() {
  for (const f of bundle.form) {
    const receipts = referenceEvidenceReceipts(f);
    if (!receipts.length) continue;
    const j = judgment(f.field_id);
    let added = 0;
    for (const receipt of receipts) {
      if (!j.selections.some((s) => s.source_id === receipt.source_id &&
          s.start_line === receipt.start_line && s.end_line === receipt.end_line &&
          s.source_hash === receipt.source_hash)) {
        j.selections.push(receipt); added++;
      }
    }
    if (added) logEvent("reference_evidence_receipts_attached", f.field_id + ":" + j.value);
  }
}
function save(submit = false) {
  attachReferenceEvidenceReceipts();
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
      if (submit && !reason.trim() && matchesSubmitted(snapshot, $("assessor").value.trim())) {
        active += elapsed;
        status();
        $("status").textContent = `Already submitted at revision ${state.last_submission}. Your current choices match that submission.`;
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
        if (submit) $("submission-blockers").hidden = true;
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
          (submit ? "Not submitted: " : "Not saved: ") +
          err.message +
          (conflicted
            ? " · reload to reconcile before continuing."
            : " · correct the fields and save again.");
        $("next").disabled = true;
        if (submit) showSubmissionBlockers();
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
  $("evidence-panel").open = true;
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
    const rawLines = view.table?.reason !== "not a table" && view.table?.lines
      ? view.table.lines.map((row) => ({...row, role: view.lines.find((r) => r.line === row.line)?.role || "context"}))
      : view.lines;
    rawLines.forEach((row) => {
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
  if (id) {
    // Starts from a copy of any supplied formula; the supplied calculation is never edited.
    const worksheet = el("button", "Start a reviewer worksheet for this item");
    const kind = bundle.claims.some((c) => c.claim_id === id) ? "claim" : "reference";
    worksheet.onclick = () => addWorksheet({ kind, id }, item.calculation);
    $("evidence").append(worksheet);
  }
  authorDeclarations(id ? [id] : [], $("evidence"));
}
// The author's declared support status: shown beside the evidence, never as support or as a citation.
const AUTHOR_DECLARATION_NOTE = "Declared by the author of the answer; not evidence, not a citation and not a support judgment. It never checks a row or changes any status.";
function authorDeclarations(claimIds, box) {
  const seen = new Set();
  claimIds.forEach((id) => {
    const claim = bundle.claims.find((c) => c.claim_id === id);
    if (!claim || !claim.support_declaration || seen.has(id)) return;
    seen.add(id);
    renderAuthorDeclaration({ claim_id: id, label: "Author's declaration", declaration: claim.support_declaration,
                              provenance: AUTHOR_DECLARATION_NOTE }, box);
  });
}
function renderAuthorDeclaration(d, box) {
  const note = el("aside"); note.className = "author-declaration"; note.dataset.declarationClaim = d.claim_id;
  note.setAttribute("aria-label", `${d.label} for statement ${d.claim_id}`);
  note.append(el("p", `${d.label}: ${d.declaration}`), el("small", d.provenance));
  box.append(note);
}
function operandContext(o) {
  return [o.entity, o.metric].filter(Boolean).join(" · ");
}
function linkedInput(o) {
  const c = o.citation;
  const href = c?.status === "located" && (evidence.links || {})[c.source_id]?.line_url
    ? originalHref(c.source_id, c.excerpt, c.start_line, c.end_line) : null;
  const value = el(href ? "a" : "span", o.value);
  value.title = operandContext(o) || o.name;
  if (href) {
    value.href = href; value.target = "_blank"; value.rel = "noopener noreferrer";
    value.onclick = () => logEvent("original_opened", c.source_id);
  }
  return value;
}
function readableFormula(c) {
  return c.formula.replace(/[A-Za-z_]\w*/g, (name) => c.operands.find((o) => o.name === name)?.value || name);
}
function compactInput(o, box, inherited = {}) {
  if (o.kind === "constant") return;
  const row = el("p"); row.className = "input-derivation";
  const labels = (o.entity || "").split(" · ");
  const sections = (o.calculation?.operands || []).map((child) => (child.entity || "").split(" · ")[1]).filter(Boolean);
  const subtotalLabel = sections.length && sections.every((v) => v === sections[0]) ? sections[0].replace(/[:*]+/g, "").trim() : null;
  const label = labels.length >= 4 ? labels[labels.length - 2] : subtotalLabel || o.name.replace(/_/g, " ");
  row.append(document.createTextNode(`${label}: `), linkedInput(o),
    document.createTextNode(`${o.unit && o.unit !== inherited.unit ? " " + o.unit : ""}${o.metric && o.metric !== inherited.metric ? " · " + o.metric : ""}${o.period && o.period !== inherited.period ? " · " + o.period : ""}`));
  if (o.calculation) {
    row.append(document.createTextNode(" = "));
    // A subtotal expression uses its own operand names; values link to exact leaves below.
    row.append(document.createTextNode(readableFormula(o.calculation)));
  }
  box.append(row);
  if (o.calculation) o.calculation.operands.forEach((child) => compactInput(child, box, o));
  else if (o.citation?.status === "located") {
    const preview = el("button", "Preview");
    preview.setAttribute("aria-label", "Preview input " + label);
    preview.className = "preview-link";
    preview.onclick = () => { $("evidence-panel").open = true; $("evidence").replaceChildren(); cite(o.citation); };
    row.append(preview);
  } else {
    const c = o.citation;
    row.append(document.createTextNode(" · source unresolved"));
    const source = c && bundle.sources.find((s) => s.source_id === c.source_id);
    if (source && Number.isInteger(c.start_line) && c.start_line >= 1 && c.end_line >= c.start_line && c.end_line <= source.text.split("\n").length) {
      const href = (evidence.links || {})[c.source_id]?.line_url ? originalHref(c.source_id, "", c.start_line, c.end_line) : null;
      const inspect = el(href ? "a" : "button", "Inspect candidate range");
      if (href) { inspect.href = href; inspect.target = "_blank"; inspect.rel = "noopener noreferrer"; }
      else inspect.onclick = () => { $("evidence-panel").open = true; $("evidence").replaceChildren(); showSource(source, c.start_line, c.end_line); };
      row.append(inspect);
    }
  }
}
function calculationTechnical(c, box) {
  const r = c.recomputation || { status: "unresolved" };
  box.append(el("p", `Formula: ${c.formula}`),
    el("p", `Reported result: ${c.result} ${c.unit}`), el("p", `Absolute tolerance: ${c.tolerance} ${c.unit}`));
  if (c.declared_tolerance) box.append(el("p", `Author's declared tolerance (display only; not used in recomputation): ${c.declared_tolerance}`));
  const constants = [...new Set(c.formula.replace(/[A-Za-z_]\w*/g, "").match(/\d+(?:\.\d+)?/g) || [])];
  constants.forEach((v) => box.append(el("p", "Formula literal (source association not established): " + v)));
  c.operands.filter((o) => o.kind === "constant").forEach((o) => box.append(el("p", "Mathematical constant: " + o.value)));
  (c.conversions || []).forEach((v) => box.append(el("p", "Conversion: " + v)));
  box.append(el("p", r.status === "unresolved" ? `Arithmetic unresolved: ${r.reason || "cannot recompute"}`
    : `Recomputed (Decimal): ${r.result} · arithmetic ${r.status} · discrepancy ${r.discrepancy}`));
  if (r.evidence) {
    box.append(el("p", r.evidence.status === "all_operands_cited"
      ? "Input evidence: every input has a located citation."
      : `Input evidence unresolved: no located citation for ${r.evidence.missing.join(", ")}.`));
    r.evidence.warnings.forEach((w) => box.append(el("p", "Warning: " + w)));
  }
}
function renderCalculation(c, box, preview = false, inputs = c.operands, checks = true) {
  box.append(el("p", `${readableFormula(c)} → reported ${c.result} ${c.unit}`));
  // The numeric tolerance recomputation uses, in the result's unit; the author's prose stays beside it.
  const tolerance = el("p", `Tolerance ±${c.tolerance}${c.unit ? " " + c.unit : ""}`); tolerance.className = "calc-tolerance";
  if (c.declared_tolerance) tolerance.append(document.createTextNode(` · author's declared tolerance: ${c.declared_tolerance} (display only)`));
  box.append(tolerance);
  inputs.forEach((o) => compactInput(o, box));
  if (checks) {
    const details = el("details"); details.append(el("summary", "Calculation checks"));
    calculationTechnical(c, details); box.append(details);
  }
}
function renderNumberCalculation(span, box, preview = false) {
  const prepared = (span.prepared_evidence || []).filter((p) => p.calculation);
  if (prepared.length) {
    box.append(el("h4", "Prepared calculation (independent)"));
    prepared.forEach((p) => renderCalculation(p.calculation, box, preview));
  } else {
    if (span.prepared_inputs?.length) box.append(el("h4", "Located inputs (independent)"));
    const inputs = span.calculation.operands.map((o) => (span.prepared_inputs || []).find((p) => p.name === o.name) || o);
    renderCalculation(span.calculation, box, preview, inputs, false);
  }
  const issues = (span.diagnostics?.candidate || []).filter((d) => d.outcome !== "consistent");
  // Older bundles still expose bad candidate citations without a diagnostic sidecar.
  if (!span.diagnostics) span.calculation.operands.forEach((o) => {
    if (o.citation?.status !== "located" && o.kind !== "constant") issues.push({scope: "candidate_input", input_path: [o.name], reason: o.citation?.reason || "source unresolved"});
  });
  const seen = new Set();
  issues.forEach((d) => {
    const scope = d.scope === "candidate_arithmetic" ? "Candidate arithmetic" : "Candidate citation";
    const reason = d.scope === "candidate_arithmetic" ? "differs from the reported result; see checks" : d.citation?.status === "excerpt_mismatch" ? "excerpt does not match cited rows" : d.reason;
    const text = `${scope}${d.input_path?.length ? " (" + d.input_path.join(" / ") + ")" : ""}: ${reason}`;
    if (!seen.has(text)) { box.append(el("p", text)); seen.add(text); }
  });
  const original = el("details"); original.append(el("summary", "Original candidate calculation"));
  calculationTechnical(span.calculation, original); box.append(original);
  renderDiagnostics(span, box);
}
function renderDiagnostics(span, box) {
  const d = span.diagnostics;
  if (!d) return;
  const panel = el("details"); panel.className = "number-diagnostics";
  panel.append(el("summary", "Technical diagnostics"));
  panel.append(el("p", "Independent preparation: " + d.preparation_status),
    el("p", "Location and arithmetic checks do not decide support, defects or materiality."));
  function issue(x) {
    const scope = {candidate_citation: "Candidate citation", candidate_input: "Candidate input", candidate_arithmetic: "Candidate arithmetic", preparation: "Preparation limitation"}[x.scope];
    panel.append(el("p", `${scope} · ${x.outcome}${x.input_path.length ? " · " + x.input_path.join(" → ") : ""}: ${x.reason}`));
    const c = x.citation;
    if (!c) return;
    panel.append(el("p", `Source ${c.source_id} · ${c.start_line == null ? "range unavailable" : "L" + c.start_line + "–L" + c.end_line} · ${c.status}`));
    const source = bundle.sources.find((s) => s.source_id === c.source_id);
    if (source) {
      const inspect = el("button", "Inspect diagnostic source " + c.source_id);
      inspect.onclick = () => {
        $("evidence-panel").open = true; $("evidence").replaceChildren();
        const valid = Number.isInteger(c.start_line) && Number.isInteger(c.end_line) && c.start_line >= 1 && c.end_line >= c.start_line && c.end_line <= source.text.split("\n").length;
        showSource(source, valid ? c.start_line : 1, valid ? c.end_line : 1);
      };
      panel.append(inspect);
    }
  }
  panel.append(el("h4", "Candidate technical checks"));
  d.candidate.forEach(issue);
  d.attempts.forEach((a, i) => {
    const earlier = a.status === "unresolved" && d.attempts.slice(i + 1).some((later) => later.status === "resolved");
    panel.append(el("h4", `${earlier ? "Earlier method — " : "Method — "}${a.method}: ${a.status}`));
    if (earlier) panel.append(el("p", "A later method supplied prepared evidence; this earlier limitation is retained as history."));
    a.diagnostics.forEach(issue);
  });
  box.append(panel);
}
function showCalculation(span, anchor) {
  const claims = span.claim_ids.filter((id) => bundle.claims.some((c) => c.claim_id === id));
  subject = claims.length === 1 ? claims[0] : null;
  selection = null;
  document.querySelectorAll(".calculation-card").forEach((n) => n.remove());
  const card = el("aside"); card.className = "calculation-card";
  card.setAttribute("role", "region"); card.setAttribute("aria-label", "Calculation details");
  renderNumberCalculation(span, card);
  authorDeclarations(span.claim_ids, card);
  const preview = el("button", "Preview calculation evidence");
  preview.onclick = () => showSpan(span, false);
  const worksheet = el("button", "Recompute in a reviewer worksheet");
  worksheet.onclick = () => addWorksheet({ kind: "span", id: span.span_id }, span.calculation);
  card.append(worksheet);
  const close = el("button", "Close calculation details"); close.onclick = () => { card.remove(); anchor.focus(); };
  card.append(preview, close); anchor.after(card);
  logEvent("span_opened", span.span_id);
}
function numberAction(s, verificationControls = true) {
  const direct = bundle.task_kind === "reference" ? null : directCitation(s);
  const prepared = bundle.task_kind === "reference" && Boolean(s.prepared_evidence?.length);
  const b = el(direct ? "a" : "button", s.text);
  if (direct) { b.href = direct.href; b.target = "_blank"; b.rel = "noopener noreferrer"; }
  b.className = "number " + (["uncited", "ambiguous", "unavailable"].includes(s.state) ? "unresolved"
    : s.state === "identifier" ? "identifier" : direct ? "direct" : "");
  if (prepared) { b.classList.remove("unresolved"); b.classList.add("prepared"); }
  const stateLabel = prepared ? "opens independently prepared evidence" : s.diagnostics ? `candidate evidence ${s.state}; preparation ${s.diagnostics.preparation_status}` : s.state;
  b.title = stateLabel + (s.reason ? ": " + s.reason : "");
  b.setAttribute("aria-label", `${s.text}, ${stateLabel}${direct ? ", opens the cited line of the original" : ""}`);
  b.onclick = () => {
    if (bundle.task_kind === "reference") showSpan(s, false);
    else if (direct) logEvent("original_opened", direct.citation.source_id);
    else if (s.calculation) showCalculation(s, b);
    else showSpan(s, false);
  };
  const verification = verificationControls && bundle.form.find((f) => f.numeric_span_id === s.span_id);
  const fragment = verification ? el("span") : document.createDocumentFragment();
  if (verification) fragment.className = "quantity-token";
  fragment.append(b);
  if (verification) {
    const label = el("label"); label.className = "quantity-check"; label.dataset.notAnswer = "";
    const check = el("input"); check.type = "checkbox";
    check.dataset.quantityField = verification.field_id;
    check.checked = answers.judgments[verification.field_id]?.value === true;
    check.setAttribute("aria-label", verification.label);
    check.title = "Checked means you verified this quantity against the evidence";
    check.onchange = () => {
      judgment(verification.field_id).value = check.checked;
      document.querySelectorAll("input[data-quantity-field]").forEach((other) => {
        if (other.dataset.quantityField === verification.field_id) other.checked = check.checked;
      });
      save(); progress();
    };
    label.append(check); fragment.prepend(label);
  }
  if (direct) {
    const preview = el("button", "Preview evidence"); preview.className = "preview-link"; preview.dataset.notAnswer = "";
    preview.setAttribute("aria-label", "Preview evidence for " + s.text);
    preview.onclick = () => showSpan(s, false); fragment.append(preview);
  }
  return fragment;
}
function linkedStatement(text, subjectId, verificationControls = !atomicMode()) {
  const wrap = el("blockquote"); wrap.className = "item-text";
  const field = bundle.fields.find((f) => f.role !== "context" && f.text === text && f.claim_ids.includes(subjectId));
  if (!field) { wrap.textContent = text; return wrap; }
  wrap.dataset.fieldPath = field.path;
  let at = 0; const chars = Array.from(text);
  bundle.spans.filter((s) => s.field_path === field.path).sort((a,b) => a.start-b.start).forEach((s) => {
    // Atomic mode owns each atom's one check control in its row; statement context here is read-only.
    wrap.append(document.createTextNode(chars.slice(at,s.start).join("")), numberAction(s, verificationControls)); at = s.end;
  });
  wrap.append(document.createTextNode(chars.slice(at).join(""))); return wrap;
}
function showSpan(span, follow = true) {
  if (bundle.task_kind === "reference") {
    subject = bundle.form.find((f) => f.numeric_span_id === span.span_id)?.subject_id
      || bundle.form.find((f) => f.field_id === current)?.subject_id || null;
    selection = null;
  }
  $("evidence-panel").open = true;
  logEvent("span_opened", span.span_id);
  if (bundle.task_kind === "reference" && span.prepared_evidence?.length) {
    const returnReference = subject;
    const back = el("button", "All evidence links"); back.onclick = () => referenceEvidenceList(returnReference);
    $("evidence").replaceChildren(back, el("h3", span.text + ": source evidence"));
    document.querySelector('[aria-label="Evidence"]').scrollTop = 0;
    if (span.context) {
      const c = span.context;
      $("evidence").append(el("p", [c.metric, c.entity, c.period, c.unit].filter(Boolean).join(" · ")));
    }
    span.prepared_evidence.forEach((p) => {
      p.citations.forEach((c) => cite(c));
      if (p.calculation) {
        $("evidence").append(el("h4", "Prepared calculation"));
        renderCalculation(p.calculation, $("evidence"), true);
      }
    });
    const provenance = el("details");
    provenance.append(el("summary", "Navigation provenance"),
      el("p", "Independently prepared navigation; not human verification. Original reference evidence remains available through Supporting factual evidence."),
      el("p", span.reason));
    span.prepared_evidence.forEach((p) => provenance.append(el("p", p.reason)));
    renderDiagnostics(span, provenance);
    $("evidence").append(provenance);
    return;
  }
  if (span.calculation) {
    const claims = span.claim_ids.filter((id) => bundle.claims.some((c) => c.claim_id === id));
    subject = claims.length === 1 ? claims[0] : null;
    selection = null;
    if (subject && follow) followSubject(subject);
    $("evidence").replaceChildren(el("h3", span.text));
    renderNumberCalculation(span, $("evidence"), true);
    authorDeclarations(span.claim_ids, $("evidence"));
    return;
  }
  const contextLine = () => {
    if (!span.context) return;
    const c = span.context;
    $("evidence").prepend(el("p", `Preparation context (${c.status}): metric ${c.metric || "unavailable"} · entity ${c.entity || "unavailable"} · period ${c.period || "unavailable"} · unit ${c.unit || "unavailable"}`));
  };
  if (span.prepared_evidence?.length) {
    $("evidence").replaceChildren(el("h3", `${span.text}: ${span.state}`), el("p", span.reason), el("p", bundle.task_kind === "reference" ? "Independently prepared navigation; original reference evidence is preserved separately." : "Prepared for review; not supplied by the answer. Candidate evidence is preserved below."));
    contextLine();
    renderDiagnostics(span, $("evidence"));
    if (span.state === "ambiguous") $("evidence").append(el("p", "Multiple possible sources—no exact match established."));
    span.prepared_evidence.forEach((p) => {
      $("evidence").append(el("p", p.reason));
      p.citations.forEach((c) => cite(c));
      if (p.calculation) { $("evidence").append(el("h4", "Prepared calculation")); renderCalculation(p.calculation, $("evidence"), true); }
    });
    $("evidence").append(el("h4", bundle.task_kind === "reference" ? "Original reference evidence" : "Candidate-supplied evidence"));
    span.citations.forEach((c) => cite(c));
    if (span.calculation) { $("evidence").append(el("h4", "Original candidate calculation")); renderCalculation(span.calculation, $("evidence"), true); }
    if (!span.citations.length && !span.calculation) $("evidence").append(el("p", bundle.task_kind === "reference" ? "Original evidence is recorded at reference level; use Supporting factual evidence." : "No source located in the answer"));
    authorDeclarations(span.claim_ids, $("evidence"));
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
  renderDiagnostics(span, $("evidence"));
}
function referenceEvidenceList(id) {
  subject = id || null; selection = null;
  $("evidence-panel").open = true;
  $("evidence").replaceChildren(el("h3", "Evidence for this reference"),
    el("p", "Open a quantity to inspect its exact source or calculation inputs."));
  const links = el("div"); links.className = "reference-evidence-links";
  bundle.form.filter((f) => f.numeric_span_id && f.subject_id === id).forEach((f) => {
    const span = bundle.spans.find((s) => s.span_id === f.numeric_span_id);
    links.append(numberAction(span, false), document.createTextNode(" "));
  });
  $("evidence").append(links);
  const item = [...bundle.references, ...bundle.claims].find((r) => (r.reference_id || r.claim_id) === id);
  if (item) {
    const facts = el("button", "Supporting factual evidence");
    facts.onclick = () => showSubject(item, id, false);
    $("evidence").append(facts);
  }
  document.querySelector('[aria-label="Evidence"]').scrollTop = 0;
}
function renderReport() {
  const report = $("report");
  if (bundle.task_kind === "reference") {
    document.querySelector('[aria-label="Report"] > h2').textContent = "Reference text";
    document.querySelector('[aria-label="Report"] > .legend').textContent = "Original reference text. Inspect evidence and verify quantities in the right pane.";
  }
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
      p.append(bundle.task_kind === "reference" ? document.createTextNode(s.text) : numberAction(s));
      offset = s.end;
    });
    p.append(document.createTextNode(chars.slice(offset).join("")));
    p.dataset.savedText = f.text;
    p.dataset.fieldPath = f.path;
    p.dataset.claimIds = JSON.stringify(f.claim_ids || []);
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
  if ((f.numeric_verification_values || []).includes(j.value) &&
      bundle.form.some((q) => (q.numeric_span_id || q.atom) && q.subject_id === f.subject_id &&
        answers.judgments[q.field_id]?.value !== true)) return false;
  if (f.note_required_unless.length && !f.note_required_unless.includes(j.value) && !j.note.trim()) return false;
  if (f.evidence_required && !j.selections.length && !referenceEvidenceReceipts(f).length) return false;
  return f.kind === "text" ? String(j.value).trim() !== "" : j.value !== "";
}
function itemFields() {
  const fields = presentationFields();
  const ids = [...new Set(fields.filter((f) => f.subject_id).map((f) => f.subject_id))];
  return ids.flatMap((id) => fields.filter((f) => f.subject_id === id && !f.numeric_span_id && !f.atom));
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
    if (f.required && !answered(f)) return f;
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
  if (item && bundle.task_kind !== "reference") showSubject(item, f.subject_id, false);
  if (bundle.task_kind === "reference") referenceEvidenceList(f?.subject_id);
  renderForms();
  if (focus) $("field-" + fieldId)?.focus();
}
function claimLinks(f) {
  // Select alongside the exact saved claim, while the issue stays in its own pane.
  document.querySelectorAll(".claim-choice").forEach((node) => node.remove());
  if (!f || !isReference(f.subject_id) || bundle.task_kind === "reference") return;
  bundle.claims.forEach((c) => {
    const target = [...$("report").querySelectorAll(".report-text")]
      .find((node) => node.dataset.savedText === c.text && JSON.parse(node.dataset.claimIds).includes(c.claim_id));
    const l = el("label");
    l.className = "claim-choice";
    const check = el("input");
    check.type = "checkbox";
    check.setAttribute("aria-label", `${c.claim_id}: ${c.text}`);
    check.checked = (answers.judgments[f.field_id]?.claim_ids || []).includes(c.claim_id);
    check.onchange = () => {
      const j = judgment(f.field_id);
      j.claim_ids = check.checked
        ? [...new Set([...j.claim_ids, c.claim_id])]
        : j.claim_ids.filter((id) => id !== c.claim_id);
      save();
    };
    l.append(check, document.createTextNode(` ${c.claim_id} addresses this issue`));
    if (target) target.before(l);
    else {
      l.append(linkedStatement(c.text, c.claim_id));
      $("report").append(l);
    }
  });
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
    const definitions = el("details");
    definitions.append(el("summary", "Judgment definitions"), dl);
    div.append(definitions);
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
  if (f.subject_id && isReference(f.subject_id) && bundle.task_kind !== "reference")
    div.append(el("p", "Select the claims that address this issue in the report pane."));
  if (bundle.task_kind === "reference")
    div.append(el("p", "Source context is attached automatically for an explained correction or uncertainty. This does not verify it or check quantities. A verified verdict still requires every quantity checked. Additional passage attachments are optional."));
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
      if (referenceEvidenceReceipts(f).some((receipt) => receipt.source_id === s.source_id &&
          receipt.source_hash === s.source_hash && receipt.start_line === s.start_line &&
          receipt.end_line === s.end_line)) {
        progress();
        $("status").textContent = "This evidence receipt is required by your current quantity checks.";
        return;
      }
      judgment(f.field_id).selections.splice(i, 1);
      renderForms();
      save();
    };
    const controls = el("span");
    controls.dataset.receiptField = f.field_id;
    controls.dataset.receiptKey = JSON.stringify([s.source_id, s.source_hash, s.start_line, s.end_line]);
    controls.append(remove, el("small", " · Automatic evidence receipt for checked quantities"));
    line.append(controls);
    div.append(line);
  }
}
function quantityProgress() {
  document.querySelectorAll("[data-quantity-subject]").forEach((node) => {
    const quantities = bundle.form.filter((q) => q.numeric_span_id && q.subject_id === node.dataset.quantitySubject);
    const verified = quantities.filter((q) => answers.judgments[q.field_id]?.value === true).length;
    node.textContent = `Quantities verified: ${verified}/${quantities.length} · ${verified === quantities.length ? "all checked; overall judgment still required" : verified ? "partially verified" : "unverified"}`;
  });
}
function progress() {
  document.querySelectorAll("[data-receipt-field]").forEach((controls) => {
    const field = bundle.form.find((f) => f.field_id === controls.dataset.receiptField);
    const automatic = field && referenceEvidenceReceipts(field).some((s) =>
      JSON.stringify([s.source_id, s.source_hash, s.start_line, s.end_line]) === controls.dataset.receiptKey);
    controls.querySelector("button").hidden = Boolean(automatic);
    controls.querySelector("button").disabled = Boolean(automatic);
    controls.querySelector("small").hidden = !automatic;
    const concern = field && field.note_required_unless.length &&
      !field.note_required_unless.includes(answers.judgments[field.field_id]?.value);
    const numeric = field && (field.numeric_verification_values || []).includes(answers.judgments[field.field_id]?.value) &&
      bundle.form.some((q) => q.numeric_span_id && q.subject_id === field.subject_id);
    controls.querySelector("small").textContent = concern || !numeric
      ? " · Automatic frozen-source context; not verification"
      : " · Automatic evidence receipt for checked quantities";
  });

  quantityProgress();
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
    `${done} of ${required.length} required fields complete` +
    (requiredFieldsOnly() ? "" : ` · ${optional} optional field${optional === 1 ? "" : "s"}`) +
    ` · ${technical} completion control${technical === 1 ? "" : "s"}` +
    (at >= 0 ? ` · item ${at + 1} of ${items.length}` : "") +
    (next ? ` · next unanswered: ${next.label}` : items.length ? " · required items answered; finish below" : "");
  const field = bundle.form.find((f) => f.field_id === current);
  const j = field && answers.judgments[field.field_id];
  const missing = [];
  if (j && (field.required || j.value !== "") && field.note_required_unless.length && !field.note_required_unless.includes(j.value) && !j.note.trim()) missing.push("explanation");
  if (j && (field.required || j.value !== "") && field.evidence_required && !j.selections.length && !referenceEvidenceReceipts(field).length) missing.push("source passage");
  if (missing.length) $("progress").textContent += " · Pending: " + missing.join(" and ");
  const button = $("next-item");
  if (button) button.textContent = next ? "Next unanswered item" : "Go to finish";
}
function showSubmissionBlockers() {
  const box = $("submission-blockers");
  const pending = bundle.form.filter((f) => (f.required || (answers.judgments[f.field_id] && answers.judgments[f.field_id].value !== "")) && !answered(f));
  box.replaceChildren(el("p", `${pending.length} fields need attention before submission. Your decisions are unchanged.`));
  pending.forEach((f) => {
    const j = answers.judgments[f.field_id];
    const note = j && f.note_required_unless.length && !f.note_required_unless.includes(j.value) && !j.note.trim();
    const b = el("button", `${note ? "Add explanation" : "Complete field"}: ${f.label}`);
    b.onclick = () => {
      box.hidden = true;
      // Existing optional decisions are preserved and still validated. Reveal
      // their repair controls only when the reviewer follows an actual blocker.
      revealedFields.add(f.field_id);
      if (f.subject_id) goTo(f.field_id);
      else { renderForms(); $("field-" + f.field_id)?.focus(); }
      if (note) document.querySelector(`[aria-label="Explanation: ${CSS.escape(f.label)}"]`)?.focus();
    };
    box.append(b);
  });
  box.hidden = !pending.length;
  if (pending.length) box.scrollIntoView({ block: "nearest" });
}
function renderForms() {
  const items = itemFields();
  if (current === null || !items.some((f) => f.field_id === current))
    current = (items.find((f) => f.required && !answered(f)) || items.find((f) => f.required) || {}).field_id ?? null;
  if (!items.some((f) => f.required) && current === null) $("item-navigation").open = false;
  $("item-navigation").hidden = requiredFieldsOnly() && !items.length;
  $("item-links").replaceChildren();
  claimLinks(bundle.form.find((f) => f.field_id === current));
  $("items").replaceChildren();
  items.forEach((f, i) => {
    const item = subjectOf(f);
    const div = el("div");
    div.className = "item" + (answered(f) ? " answered" : "");
    if (f.field_id !== current) {
      const j = answers.judgments[f.field_id];
      const saved = j && j.value !== "";
      const pending = j && f.note_required_unless.length && !f.note_required_unless.includes(j.value) && !j.note.trim()
        ? "needs explanation" : "incomplete";
      const state = answered(f) ? "answered" : saved ? `selected · ${pending}` : f.required ? "unanswered" : "optional";
      const open = el(
        "button",
        `${i + 1}. ${f.label} · ${state}${item ? " · " + short(item.text, 70) : ""}`,
      );
      open.className = "item-link";
      open.onclick = () => goTo(f.field_id);
      div.append(open);
      $("item-links").append(div);
      return;
    }
    div.classList.add("current");
    div.setAttribute("aria-label", "Current item");
    div.append(el("h3", `Item ${i + 1} of ${items.length}: ${f.label}`));
    if (item) {
      div.append(el("p", isReference(f.subject_id) ? "Reference item:" : "Statement under review:"));
      div.append(linkedStatement(item.text, f.subject_id));
      if (bundle.task_kind === "reference") {
        const facts = el("button", "Supporting factual evidence");
        facts.onclick = () => showSubject(item, f.subject_id, false);
        div.append(facts);
      }
      div.append(el("small", bundle.task_kind === "reference" ? "Verify the reference against its sources, units, period and arithmetic." : isReference(f.subject_id) ? "Coverage: decide whether the answer addresses this reference." : "Evidence support: decide whether the source supports the statement as written."));
    }
    const quantities = bundle.form.filter((q) => q.numeric_span_id && q.subject_id === f.subject_id);
    if (quantities.length) {
      const summary = el("p"); summary.dataset.quantitySubject = f.subject_id;
      div.append(summary);
    }
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
  $("forms").replaceChildren();
  presentationFields()
    .filter((f) => !f.subject_id && !f.numeric_span_id && !f.atom)
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
    const answerAttach = el("button", "Attach selected answer text to defect");
    answerAttach.onclick = () => {
      if (!answerSelection) {
        $("status").textContent = "Select exact text in an answer field first.";
        return;
      }
      d.answer_ranges = [...(d.answer_ranges || []), structuredClone(answerSelection)];
      renderDefects();
      save();
    };
    div.append(answerAttach);
    if (!(d.answer_ranges || []).length)
      div.append(el("small", "No answer passage attached: this records an omission or a whole-answer defect."));
    (d.answer_ranges || []).forEach((r, k) => {
      div.append(rangeInContext(r));
      const drop = el("button", "Remove answer text");
      drop.onclick = () => {
        d.answer_ranges.splice(k, 1);
        // An empty list is the legacy shape; never store it.
        if (!d.answer_ranges.length) delete d.answer_ranges;
        renderDefects();
        save();
      };
      div.append(drop);
    });
    const remove = el("button", "Remove defect");
    remove.onclick = () => {
      answers.defects.splice(i, 1);
      // A worksheet about this defect keeps its calculation and loses only the link.
      (answers.worksheets || []).forEach((w) => {
        if (w.subject?.kind === "defect" && w.subject.id === d.defect_id) w.subject = null;
      });
      renderDefects();
      save();
    };
    div.append(remove);
    $("defects").append(div);
  });
  renderWorksheets();
}
// ---- Reviewer-authored calculation worksheets (reviewer-calculation-worksheet/v1).
// Separate from candidate and prepared calculations: a worksheet never edits a
// supplied formula, and its server-derived arithmetic never decides support.
function worksheetInputs(a) {
  return canonical((a?.worksheets || []).map(({ computation, ...inputs }) => inputs));
}
function newWorksheetOperand(name, from = {}) {
  return { name, value: from.value || "", unit: from.unit || "", period: from.period || "",
    entity: from.entity || "", metric: from.metric || "", availability: "selected",
    unavailable_reason: "", selections: [] };
}
function newWorksheet(subjectRef, from = null) {
  return { worksheet_id: "W" + crypto.randomUUID(), contract: "reviewer-calculation-worksheet/v1",
    authorship: "reviewer", subject: subjectRef, formula: from?.formula || "",
    operands: (from?.operands || []).filter((o) => o.kind !== "constant").map((o) => newWorksheetOperand(o.name, o)),
    reported_value: from?.result || "", unit: from?.unit || "", tolerance: from?.tolerance || "0",
    rationale: "", reviewer_support: "unknown" };
}
function addWorksheet(subjectRef, from = null) {
  (answers.worksheets ||= []).push(newWorksheet(subjectRef, from));
  renderWorksheets();
  save();
  const all = document.querySelectorAll("#worksheets .worksheet");
  all[all.length - 1]?.querySelector("input")?.focus();
}
function worksheetSubjects() {
  const out = [];
  bundle.claims.forEach((c) => out.push({ kind: "claim", id: c.claim_id, label: `Claim ${c.claim_id}: ${short(c.text, 60)}` }));
  bundle.references.forEach((r) => out.push({ kind: "reference", id: r.reference_id, label: `Reference ${r.reference_id}: ${short(r.text, 60)}` }));
  bundle.spans.filter((n) => n.state !== "identifier").forEach((n) =>
    out.push({ kind: "span", id: n.span_id, label: `Number ${n.text} (${n.field_path})` }));
  answers.defects.forEach((d, i) => out.push({ kind: "defect", id: d.defect_id, label: `Defect ${i + 1}${d.category ? ": " + d.category : ""}` }));
  (answers.annotations || []).forEach((a, i) => out.push({ kind: "annotation", id: a.annotation_id,
    label: `Annotation ${i + 1}: “${short(a.answer_range.text, 50)}”` }));
  return out;
}
function suppliedCalculation(ref) {
  if (!ref) return null;
  if (ref.kind === "claim") return bundle.claims.find((c) => c.claim_id === ref.id)?.calculation || null;
  if (ref.kind === "reference") return bundle.references.find((r) => r.reference_id === ref.id)?.calculation || null;
  if (ref.kind === "span") return bundle.spans.find((n) => n.span_id === ref.id)?.calculation || null;
  return null;
}
function worksheetText(w, label, key, box, aria = label) {
  const l = el("label", label + " ");
  const input = el("input");
  input.setAttribute("aria-label", aria);
  input.value = w[key];
  input.oninput = () => { w[key] = input.value; showWorksheetResults(); save(); };
  l.append(input); box.append(l);
  return input;
}
function renderWorksheets() {
  const box = $("worksheets");
  if (!box) return;
  box.replaceChildren();
  (answers.worksheets || []).forEach((w, i) => {
    const n = i + 1, div = el("div");
    div.className = "worksheet";
    div.dataset.worksheet = w.worksheet_id;
    div.setAttribute("role", "group");
    div.setAttribute("aria-label", `Worksheet ${n}`);
    div.append(el("h4", `Worksheet ${n} · reviewer-authored calculation`));
    const subjectLabel = el("label", "About ");
    const pick = el("select");
    pick.setAttribute("aria-label", `Worksheet ${n} subject`);
    pick.append(new Option("Choose what this calculation checks…", ""));
    const choices = worksheetSubjects();
    if (w.subject && !choices.some((c) => c.kind === w.subject.kind && c.id === w.subject.id))
      choices.push({ ...w.subject, label: `${w.subject.kind} ${w.subject.id}` });
    choices.forEach((c) => pick.append(new Option(c.label, c.kind + "\u0000" + c.id)));
    pick.value = w.subject ? w.subject.kind + "\u0000" + w.subject.id : "";
    pick.onchange = () => {
      const [kind, id] = pick.value.split("\u0000");
      w.subject = pick.value ? { kind, id } : null;
      renderWorksheets(); save();
    };
    subjectLabel.append(pick); div.append(subjectLabel);
    const supplied = suppliedCalculation(w.subject);
    div.append(el("p", supplied
      ? `Supplied formula (candidate, unchanged): ${supplied.formula} → reported ${supplied.result} ${supplied.unit}`.trim()
      : "No supplied formula for this subject."));
    worksheetText(w, "Formula", "formula", div, `Worksheet ${n} formula`);
    div.append(el("small", "Use operand names, numbers, + − × ÷ (as + - * /) and parentheses."));
    w.operands.forEach((o, j) => {
      const row = el("fieldset");
      row.className = "worksheet-operand";
      const legend = el("legend", `Operand ${o.name || j + 1}`);
      row.append(legend);
      for (const [key, label] of [["name", "name"], ["value", "value"], ["unit", "unit"], ["period", "period"],
                                  ["entity", "entity"], ["metric", "metric"]]) {
        const input = worksheetText(o, label, key, row, `Worksheet ${n} operand ${j + 1} ${label}`);
        if (key === "name") input.addEventListener("input", () => { legend.textContent = `Operand ${o.name || j + 1}`; });
        if (key === "value") input.disabled = o.availability === "unavailable";
      }
      const availability = el("select");
      availability.setAttribute("aria-label", `Worksheet ${n} operand ${j + 1} availability`);
      availability.append(new Option("Value from source passages", "selected"), new Option("Unavailable in the sources", "unavailable"));
      availability.value = o.availability;
      availability.onchange = () => {
        o.availability = availability.value;
        if (o.availability === "unavailable") { o.value = ""; o.selections = []; }
        renderWorksheets(); save();
      };
      row.append(availability);
      if (o.availability === "unavailable") {
        worksheetText(o, "Why unavailable", "unavailable_reason", row, `Worksheet ${n} operand ${j + 1} unavailable reason`);
      } else {
        const attach = el("button", "Attach selected passage");
        attach.type = "button";
        attach.setAttribute("aria-label", `Attach selected passage to worksheet ${n} operand ${j + 1}`);
        attach.onclick = () => {
          if (!selection) { $("status").textContent = "Select source lines first, then attach them."; return; }
          o.selections.push(structuredClone(selection));
          renderWorksheets(); save();
        };
        row.append(attach);
        o.selections.forEach((sel, k) => {
          const p = el("p", `${sel.source_id} L${sel.start_line}–L${sel.end_line}: ${sel.excerpt}`);
          const drop = el("button", "Remove passage");
          drop.type = "button";
          drop.setAttribute("aria-label", `Remove passage ${k + 1} from worksheet ${n} operand ${j + 1}`);
          drop.onclick = () => { o.selections.splice(k, 1); renderWorksheets(); save(); };
          p.append(drop); row.append(p);
        });
        if (!o.selections.length) row.append(el("p", "No source passage attached."));
      }
      const removeOperand = el("button", "Remove operand");
      removeOperand.type = "button";
      removeOperand.setAttribute("aria-label", `Remove worksheet ${n} operand ${j + 1}`);
      removeOperand.onclick = () => { w.operands.splice(j, 1); renderWorksheets(); save(); };
      row.append(removeOperand);
      div.append(row);
    });
    const addOperand = el("button", "Add operand");
    addOperand.type = "button";
    addOperand.setAttribute("aria-label", `Add operand to worksheet ${n}`);
    addOperand.onclick = () => {
      let k = w.operands.length + 1;
      while (w.operands.some((o) => o.name === "input" + k)) k++;
      w.operands.push(newWorksheetOperand("input" + k));
      renderWorksheets(); save();
    };
    div.append(addOperand);
    worksheetText(w, "Reported value", "reported_value", div, `Worksheet ${n} reported value`);
    worksheetText(w, "Unit", "unit", div, `Worksheet ${n} unit`);
    worksheetText(w, "Absolute tolerance", "tolerance", div, `Worksheet ${n} tolerance`);
    const result = el("output");
    result.className = "worksheet-result";
    result.setAttribute("aria-live", "polite");
    result.setAttribute("aria-label", `Worksheet ${n} result`);
    div.append(result);
    const rationale = el("textarea");
    rationale.setAttribute("aria-label", `Worksheet ${n} rationale`);
    rationale.placeholder = "What this calculation checks and why these inputs";
    rationale.value = w.rationale;
    rationale.oninput = () => { w.rationale = rationale.value; save(); };
    div.append(rationale);
    const support = el("select");
    support.setAttribute("aria-label", `Worksheet ${n} reviewer support judgment`);
    support.append(new Option("Support: unknown (not decided by arithmetic)", "unknown"),
      new Option("Support: supported", "supported"), new Option("Support: unsupported", "unsupported"));
    support.value = w.reviewer_support;
    support.onchange = () => { w.reviewer_support = support.value; save(); };
    div.append(support);
    const remove = el("button", "Remove worksheet");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove worksheet ${n}`);
    remove.onclick = () => { answers.worksheets.splice(i, 1); renderWorksheets(); save(); };
    div.append(remove);
    box.append(div);
  });
  showWorksheetResults();
}
function describeComputation(c) {
  if (!c) return "Not yet recomputed.";
  if (c.status === "computed") {
    const compared = c.comparison === "not_compared" ? "no reported value compared"
      : `reported value ${c.comparison} · discrepancy ${c.discrepancy}`;
    return `Recomputed (Decimal): ${c.result} · ${compared}`;
  }
  return { calculation_error: "Calculation error: ", incomplete: "Incomplete: ", invalid: "Not computed: " }[c.status] + c.reason;
}
function showWorksheetResults() {
  const saved = new Map((state?.answers?.worksheets || []).map((w) => [w.worksheet_id, w]));
  document.querySelectorAll("#worksheets .worksheet").forEach((div) => {
    const out = div.querySelector(".worksheet-result");
    const mine = (answers.worksheets || []).find((w) => w.worksheet_id === div.dataset.worksheet);
    const theirs = saved.get(div.dataset.worksheet);
    const current = mine && theirs &&
      JSON.stringify(worksheetInputs({ worksheets: [mine] })) === JSON.stringify(worksheetInputs({ worksheets: [theirs] }));
    out.textContent = current
      ? describeComputation(theirs.computation) + " · Reviewer arithmetic; it does not establish support."
      : "Not recomputed yet: these inputs are not saved (see save status).";
    out.dataset.status = current ? theirs.computation.status : "unsaved";
  });
}
// ---- answer-annotation/v1: reviewer-selected exact text in an original answer field.
// Offsets are Unicode code points (Array.from), identical to Python str indexing;
// the store re-validates field, document digests, bounds and exact text.
const DISPOSITIONS = [
  ["supported", "Supported by the sources"],
  ["defective", "Defective"],
  ["cannot_verify", "Cannot verify"],
  ["incomplete", "Incomplete"],
];
const MATERIAL_DISPOSITIONS = ["defective", "incomplete"];
function codePoints(text) {
  return Array.from(text).length;
}
// Code-point offset of a DOM boundary within one answer host, counting only the
// answer's own text (never control labels such as "Preview evidence").
function answerOffset(host, node, offset) {
  const point = document.createRange();
  point.setStart(node, offset);
  point.collapse(true);
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => n.parentElement.closest("[data-not-answer]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  let count = 0;
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    if (t === node) {
      const before = t.data.slice(0, offset);
      if (/[\uD800-\uDBFF]$/.test(before)) return null; // never split a surrogate pair
      return count + codePoints(before);
    }
    if (point.comparePoint(t, 0) >= 0) return count;
    count += codePoints(t.data);
  }
  return count;
}
function answerHost(node) {
  const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  return element?.closest("[data-field-path]") || null;
}
function selectedAnswerRange() {
  const sel = document.getSelection();
  if (!bundle || !sel || sel.isCollapsed || !sel.rangeCount) return undefined;
  const range = sel.getRangeAt(0);
  const host = answerHost(range.startContainer), end = answerHost(range.endContainer);
  if (!host && !end) return undefined; // a selection elsewhere leaves the last answer selection intact
  if (!host || host !== end) return { error: "Select text within one answer field." };
  const field = annotatableFields().find((f) => f.path === host.dataset.fieldPath);
  if (!field) return { error: "That text is not part of an answer under review." };
  const start = answerOffset(host, range.startContainer, range.startOffset);
  const stop = answerOffset(host, range.endContainer, range.endOffset);
  if (start === null || stop === null || !(start >= 0 && start < stop && stop <= codePoints(field.text)))
    return { error: "That selection has no exact answer text." };
  return rangeFor(field, start, stop);
}
// Answer fields the server bound for annotation (never context fields).
function annotatableFields() {
  const binding = evidence.answer_annotation;
  return bundle.fields.filter((f) => f.role !== "context" && binding?.fields?.[f.path]);
}
// The one range shape both the mouse and the keyboard path produce: code points [start, stop).
function rangeFor(field, start, stop) {
  const binding = evidence.answer_annotation;
  const chars = Array.from(field.text);
  return {
    contract: binding.contract,
    field_path: field.path,
    start,
    end: stop,
    text: chars.slice(start, stop).join(""),
    offset_unit: binding.offset_unit,
    field_sha256: binding.fields[field.path],
    documents_sha256: binding.documents_sha256,
  };
}
function describeRange(r) {
  const field = bundle.fields.find((f) => f.path === r.field_path);
  return `${field ? field.label : r.field_path}, characters ${r.start + 1}–${r.end}`;
}
// Keyboard path: every exact occurrence of typed text in one answer field, as code points.
// indexOf counts UTF-16 units; a well-formed needle only matches at a code-point boundary.
function typedOccurrences(field, needle) {
  const out = [];
  if (!needle) return out;
  for (let i = field.text.indexOf(needle); i !== -1; i = field.text.indexOf(needle, i + 1)) {
    const start = codePoints(field.text.slice(0, i));
    out.push([start, start + codePoints(needle)]);
  }
  return out;
}
function renderTypedOccurrences() {
  const field = annotatableFields().find((f) => f.path === $("annotate-field").value);
  const needle = $("annotate-text").value;
  const pick = $("annotate-occurrence");
  const found = field ? typedOccurrences(field, needle) : [];
  pick.replaceChildren();
  if (!found.length)
    pick.append(new Option(needle ? "No exact occurrence in this field" : "Type exact text first", ""));
  const chars = field ? Array.from(field.text) : [];
  found.forEach(([s, e], k) => {
    const before = chars.slice(Math.max(0, s - 20), s).join(""), after = chars.slice(e, e + 20).join("");
    pick.append(new Option(`Occurrence ${k + 1} of ${found.length} (characters ${s + 1}–${e}): …${before}[${chars.slice(s, e).join("")}]${after}…`, `${s}:${e}`));
  });
}
function renderTypedAnnotation() {
  const fields = annotatableFields();
  $("annotate-typed").hidden = !fields.length;
  const pick = $("annotate-field"), keep = pick.value;
  pick.replaceChildren(...fields.map((f) => new Option(f.label || f.path, f.path)));
  if (fields.some((f) => f.path === keep)) pick.value = keep;
  renderTypedOccurrences();
}
$("annotate-field").onchange = renderTypedOccurrences;
$("annotate-text").oninput = renderTypedOccurrences;
$("use-typed-text").onclick = () => {
  const field = annotatableFields().find((f) => f.path === $("annotate-field").value);
  const chosen = $("annotate-occurrence").value;
  const box = $("answer-selection");
  if (!field || !chosen) {
    answerSelection = null;
    box.textContent = "No occurrence chosen: type text that occurs exactly in the chosen answer field.";
    return;
  }
  const [start, stop] = chosen.split(":").map(Number);
  answerSelection = rangeFor(field, start, stop);
  box.textContent = `Selected answer text (${describeRange(answerSelection)}): “${short(answerSelection.text, 120)}”`;
};
document.addEventListener("selectionchange", () => {
  const picked = selectedAnswerRange();
  if (picked === undefined) return;
  const box = $("answer-selection");
  if (picked.error) {
    answerSelection = null;
    box.textContent = picked.error;
    return;
  }
  answerSelection = picked;
  box.textContent = `Selected answer text (${describeRange(picked)}): “${short(picked.text, 120)}”`;
});
// The chosen occurrence in its surroundings, so repeated words are distinguishable.
function rangeInContext(r) {
  const field = bundle.fields.find((f) => f.path === r.field_path);
  const chars = Array.from(field ? field.text : r.text);
  const quote = el("blockquote");
  quote.className = "annotated-text";
  const lead = Math.max(0, r.start - 40), tail = Math.min(chars.length, r.end + 40);
  const mark = el("mark", r.text);
  quote.append((lead ? "…" : "") + chars.slice(lead, r.start).join(""), mark,
    chars.slice(r.end, tail).join("") + (tail < chars.length ? "…" : ""));
  quote.setAttribute("aria-label", "Annotated answer text: " + describeRange(r));
  return quote;
}
function renderAnnotations() {
  const list = $("annotations");
  list.replaceChildren();
  answers.annotations.forEach((a, i) => {
    const div = el("div");
    div.className = "annotation";
    div.dataset.annotationId = a.annotation_id;
    div.append(el("h4", `Annotation ${i + 1} · ${describeRange(a.answer_range)}`), rangeInContext(a.answer_range));
    const disposition = el("select");
    disposition.setAttribute("aria-label", "Annotation disposition");
    disposition.append(new Option("Choose disposition…", ""));
    DISPOSITIONS.forEach(([value, label]) => disposition.append(new Option(label, value)));
    disposition.value = a.disposition || "";
    const material = el("select");
    material.setAttribute("aria-label", "Annotation materiality");
    material.append(new Option("Choose materiality…", ""), new Option("Nonmaterial", "false"), new Option("Material", "true"));
    material.value = a.material === null ? "" : String(a.material);
    const applicable = () => { material.hidden = !MATERIAL_DISPOSITIONS.includes(a.disposition); };
    disposition.onchange = () => {
      a.disposition = disposition.value || null;
      // Materiality is recorded only where a problem is reported.
      if (!MATERIAL_DISPOSITIONS.includes(a.disposition)) { a.material = null; material.value = ""; }
      applicable();
      save();
    };
    material.onchange = () => {
      a.material = material.value === "" ? null : material.value === "true";
      save();
    };
    applicable();
    const reason = el("textarea");
    reason.setAttribute("aria-label", "Annotation reason");
    reason.placeholder = "Reason (required unless supported)";
    reason.value = a.reason;
    reason.oninput = () => { a.reason = reason.value; save(); };
    div.append(disposition, material, reason);
    const attach = el("button", "Attach selected source passage to annotation");
    attach.onclick = () => {
      if (!selection) {
        $("status").textContent = "Select a passage first: open a source and click its first and last line.";
        return;
      }
      a.selections.push(structuredClone(selection));
      renderAnnotations();
      save();
    };
    div.append(attach);
    a.selections.forEach((s, k) => {
      const line = el("p", `${s.source_id} L${s.start_line}–L${s.end_line}: ${s.excerpt}`);
      const remove = el("button", "Remove passage");
      remove.onclick = () => { a.selections.splice(k, 1); renderAnnotations(); save(); };
      line.append(remove);
      div.append(line);
    });
    const start = el("button", "Start calculation worksheet");
    start.type = "button";
    start.setAttribute("aria-label", `Start calculation worksheet from annotation ${i + 1}`);
    start.onclick = () => addWorksheet({ kind: "annotation", id: a.annotation_id });
    div.append(start);
    const refused = el("p");
    refused.className = "save-error";
    refused.setAttribute("role", "alert");
    refused.hidden = true;
    const remove = el("button", "Remove annotation");
    remove.onclick = () => {
      // A worksheet's subject must exist in the same answers (the store refuses a dangling
      // link on save and submit alike), so a linked annotation is never removed silently.
      const linked = annotationWorksheets(a.annotation_id);
      if (linked.length) {
        refused.hidden = false;
        refused.textContent = `Not removed: ${linked.map((n) => "worksheet " + n).join(", ")} ` +
          `${linked.length > 1 ? "are" : "is"} about this annotation. Change that worksheet's subject or remove it first.`;
        return;
      }
      answers.annotations.splice(i, 1);
      renderAnnotations();
      renderWorksheets();
      save();
    };
    div.append(remove, refused);
    list.append(div);
  });
}
// 1-based numbers of the worksheets whose subject is this annotation.
function annotationWorksheets(id) {
  return (answers.worksheets || []).flatMap((w, n) =>
    w.subject?.kind === "annotation" && w.subject.id === id ? [n + 1] : []);
}
function annotateSelection() {
  if (!answerSelection) {
    $("status").textContent = "Select exact text in an answer field first.";
    return;
  }
  answers.annotations.push({
    contract: answerSelection.contract,
    annotation_id: "ann:" + crypto.randomUUID(),
    answer_range: structuredClone(answerSelection),
    disposition: null,
    reason: "",
    material: null,
    selections: [],
  });
  renderAnnotations();
  renderWorksheets();
  save();
  $("annotations").lastElementChild?.querySelector("select")?.focus();
}
function render() {
  $("review-type").textContent = {
    independent: "Answer grading",
    reference: "Source verification",
    adjudication: "Disagreement review",
    finding: "Finding review",
  }[bundle.task_kind] || "Evidence Review";
  $("task").textContent = bundle.bundle_id;
  $("workload").replaceChildren();
  $("workload-summary").textContent = "";
  $("workload-summary").hidden = true;
  if (evidence.workload || bundle.workload) {
    const w = evidence.workload || bundle.workload;
    // A caller-defined generic noun ("Check 1 of 3") replaces the package default.
    const label = w.task_noun || (["independent", "adjudication"].includes(bundle.task_kind) ? "Answer" : "Task");
    $("workload-summary").textContent = `${label} ${w.answer_index} of ${w.assigned_answers}`;
    $("workload-summary").hidden = false;
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
  const guide = $("resource-guide"); guide.replaceChildren(el("h2", "Resource guide"));
  guide.append(el("p", "Sources remain the authority. The exact saved answer appears in the response pane as its original summary, structured statements and qualifications. Candidate citations and formulas preserve missing or invalid evidence. Number links expose inputs and deterministic arithmetic; prepared evidence is separately attributed and is not a support verdict."));
  guide.append(el("h3", "Every eligible frozen source"));
  bundle.sources.forEach((s) => {
    const row = el("p", `${s.title} · type: ${s.metadata.document_type || s.metadata.type || s.metadata.kind || "not supplied"} · period: ${s.metadata.period || "not supplied"} · SHA-256 ${s.sha256}`);
    const preview = el("button", "Full frozen preview: " + s.title);
    preview.onclick = () => { $("resource-dialog").close(); $("evidence").replaceChildren(); showSource(s); };
    row.append(preview); guide.append(row);
  });
  guide.append(el("h3", "Reference verification status"), el("p", "A reference checklist is not gold truth. Verification is not established unless explicitly supplied in task context; check its evidence against the frozen sources."));
  const refs = el("details"); refs.append(el("summary", `Required-issue / reference checklist (${bundle.references.length})`));
  bundle.references.forEach((r, i) => refs.append(el("p", `Required issue ${i + 1} (${r.reference_id}): ${r.text}`))); guide.append(refs);
  const rubric = el("details"); rubric.append(el("summary", "Review rubric and judgment definitions"));
  presentationFields().forEach((f) => {
    rubric.append(el("h4", f.label), el("p", f.help));
    Object.entries(f.option_help || {}).forEach(([option, meaning]) => rubric.append(el("p", `${option}: ${meaning}`)));
  }); guide.append(rubric);
  const inv = evidence.inventory;
  if (inv && bundle.task_kind !== "reference") {
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
  if (first && subjectOf(first) && bundle.task_kind !== "reference") showSubject(subjectOf(first), first.subject_id, false);
  if (bundle.task_kind === "reference") referenceEvidenceList(first?.subject_id);
  $("evidence-panel").open = true;
  renderDefects();
  renderAnnotations();
  renderTypedAnnotation();
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
  if (atomicMode()) renderAtomic();
  status();
}
async function load() {
  bundle = await api("/api/bundle");
  evidence = await api("/api/evidence");
  state = await api("/api/state");
  conflicted = false;
  $("conflict").hidden = true;
  answers = structuredClone(state.answers);
  answers.annotations ||= [];
  answerSelection = null;
  $("answer-selection").textContent = "No answer text selected.";
  answers.worksheets ||= [];
  revealedFields.clear();
  $("assessor").value =
    state.assessor ||
    state.suggested_assessor ||
    tabStore.get("assessor") ||
    "";
  queue = Promise.resolve();
  render();
}
$("open-guide").onclick = () => $("review-guide").showModal();
$("close-guide").onclick = () => $("review-guide").close();
$("open-resources").onclick = () => $("resource-dialog").showModal();
$("close-resources").onclick = () => $("resource-dialog").close();
$("assessor").oninput = () =>
  tabStore.set("assessor", $("assessor").value.trim());
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
$("add-annotation").onclick = annotateSelection;
$("add-worksheet").onclick = () => {
  const claim = subject && bundle.claims.some((c) => c.claim_id === subject) ? { kind: "claim", id: subject } : null;
  addWorksheet(claim);
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
  invalidateNavigation();
  renderCache.clear();
  $("viewer").replaceChildren();
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
  } else if (event.key === "Escape" && (selection || answerSelection)) {
    selection = null;
    answerSelection = null;
    $("answer-selection").textContent = "No answer text selected.";
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
// ---- atomic-source-check/v1: statements above, one row per atom left, rendered source right.
const renderCache = new Map();
let viewerOrigin = null;
// Navigation fence: every source open takes a new generation and captures the
// task it was made for. A response or failure that arrives after a newer open,
// or after the task changed, never paints or moves focus, even if abort failed.
let navigation = 0, taskEpoch = 0, inflight = null;
function invalidateNavigation() {
  navigation += 1; taskEpoch += 1;
  if (inflight) { try { inflight.controller.abort(); } catch (_) { /* fenced regardless */ } }
  inflight = null;
}
function atomicMode() {
  return evidence.presentation?.mode === "atomic-source-check/v1" && Boolean(evidence.atoms);
}
const ATOM_STATE = {
  located: "Source located",
  derived: "Calculated from inputs",
  ambiguous: "Ambiguous: several candidate sources, none chosen",
  unavailable: "No source located",
  unsupported: "No supporting source identified",
};
// Where each row target's binding to this exact occurrence comes from.
const BINDING = {
  span_citation: "cited by the answer for this number",
  prepared_evidence: "independently prepared evidence attributed to this number",
  prepared_calculation_input: "input of an independently prepared calculation attributed to this occurrence",
  prepared_input: "independently located input of this occurrence's own calculation",
  linked_statement_citation: "cited by the statement this fact belongs to",
  declared_atom_citation: "declared for this fact by the caller's atomization",
};
function atomField(row) {
  return row.form_field_id ? bundle.form.find((f) => f.field_id === row.form_field_id) : null;
}
function renderAtomicStatements() {
  const report = $("report");
  report.replaceChildren();
  document.querySelector('[aria-label="Report"] > h2').textContent = "Statements under review (unedited)";
  document.querySelector('[aria-label="Report"] > .legend').textContent =
    "Exact saved text. Underlined passages are the atoms listed below; select one to go to its row.";
  const rows = evidence.atoms.rows;
  bundle.fields.filter((f) => f.role !== "context").forEach((f) => {
    report.append(el("h3", f.label));
    const p = el("div"); p.className = "report-text"; p.dataset.savedText = f.text; p.dataset.fieldPath = f.path;
    const chars = Array.from(f.text);
    let at = 0;
    rows.filter((r) => r.field_path === f.path).sort((a, b) => a.start - b.start).forEach((r) => {
      if (r.start < at) return; // Overlap is refused upstream; never redraw text twice.
      p.append(document.createTextNode(chars.slice(at, r.start).join("")));
      const mark = el("button", chars.slice(r.start, r.end).join(""));
      mark.type = "button"; mark.className = "atom-mark " + r.evidence_state; mark.dataset.atomMark = r.atom_id;
      mark.setAttribute("aria-label", `${r.text}: go to its check row (${ATOM_STATE[r.evidence_state]})`);
      mark.onclick = () => document.querySelector(`[data-atom-row="${CSS.escape(r.atom_id)}"] .atom-focus`)?.focus();
      p.append(mark);
      at = r.end;
    });
    p.append(document.createTextNode(chars.slice(at).join("")));
    report.append(p);
  });
  const context = evidence.atoms.atomization.context_span_ids.length;
  if (context) report.append(el("p", `${context} other number${context === 1 ? " is" : "s are"} context only and not assigned for checking.`));
}
function describeTarget(t) {
  if (!t) return "no target";
  const where = t.page ? `page ${t.page}` : t.start_line ? (t.end_line && t.end_line !== t.start_line ? `L${t.start_line}–L${t.end_line}` : `L${t.start_line}`) : "";
  const source = bundle.sources.find((s) => s.source_id === t.source_id);
  return `${source ? source.title : t.source_id}${where ? " " + where : ""}`;
}
// Prepared evidence is shown beside the original candidate evidence, never in its place.
function renderPreparation(p, r, more) {
  const box = el("section"); box.className = "atom-preparation"; box.dataset.preparedRef = p.ref;
  box.setAttribute("aria-label", `${p.label} for “${r.text}”`);
  box.append(el("h4", p.label), el("p", p.provenance));
  if (p.reason) box.append(el("p", "Preparation note: " + p.reason));
  box.append(el("small", `Reference ${p.ref}`));
  p.targets.forEach((t, k) => {
    const line = el("p", `Prepared source ${k + 1}: ${describeTarget(t)}`);
    const open = el("button", "Open prepared source");
    open.setAttribute("aria-label", `Open prepared source ${k + 1} for “${r.text}”`);
    open.onclick = () => openTarget(t, open, r, "Independently prepared evidence; not a support judgment");
    line.append(open); box.append(line);
  });
  if (p.calculation) {
    renderCalculation(p.calculation, box, true, p.calculation.operands, true);
    if (p.calculation_leaves.length) box.append(el("h5", "Prepared input sources"));
    p.calculation_leaves.forEach((leaf, k) => {
      const line = el("p", `Prepared input ${k + 1}: ${leaf.target ? describeTarget(leaf.target) : leaf.source_id} · ${leaf.status}${leaf.reason ? ": " + leaf.reason : ""}`);
      if (leaf.target) {
        const open = el("button", "Open prepared input source");
        open.setAttribute("aria-label", `Open prepared input ${k + 1} source for “${r.text}”`);
        open.onclick = () => openTarget(leaf.target, open, r, "Input of an independently prepared calculation");
        line.append(open);
      }
      box.append(line);
    });
  }
  more.append(box);
}
// Independently located inputs of the original calculation, beside (never replacing) the original inputs.
function renderPreparedInputs(inputs, r, more) {
  if (!inputs.length) return;
  const box = el("section"); box.className = "atom-prepared-inputs";
  box.setAttribute("aria-label", `Independently located inputs for “${r.text}”`);
  box.append(el("h4", "Independently located inputs"), el("p", inputs[0].provenance));
  inputs.forEach((o) => {
    const item = el("div"); item.className = "atom-prepared-input"; item.dataset.preparedInputRef = o.ref;
    const what = [o.value + (o.unit ? " " + o.unit : ""), o.metric, o.period, o.entity].filter(Boolean).join(" · ");
    item.append(el("h5", `${o.label}: ${what}`));
    if (o.original) {
      item.append(el("small", `Original candidate input: ${o.original.status}${o.original.reason ? ": " + o.original.reason : ""}`));
    }
    if (o.source) {
      const line = el("p", `Located source: ${o.source.target ? describeTarget(o.source.target) : o.source.source_id} · ${o.source.status}${o.source.reason ? ": " + o.source.reason : ""}`);
      if (o.source.target) {
        const open = el("button", "Open located input source");
        open.setAttribute("aria-label", `Open the located source for input “${o.name}” of “${r.text}”`);
        open.onclick = () => openTarget(o.source.target, open, r, "Independently located input; not a support judgment");
        line.append(open);
      }
      item.append(line);
    }
    if (o.calculation) {
      renderCalculation(o.calculation, item, true, o.calculation.operands, true);
      o.calculation_leaves.forEach((leaf, k) => {
        const line = el("p", `Input “${o.name}” part ${k + 1}: ${leaf.target ? describeTarget(leaf.target) : leaf.source_id} · ${leaf.status}${leaf.reason ? ": " + leaf.reason : ""}`);
        if (leaf.target) {
          const open = el("button", "Open part source");
          open.setAttribute("aria-label", `Open part ${k + 1} source of input “${o.name}” for “${r.text}”`);
          open.onclick = () => openTarget(leaf.target, open, r, "Part of an independently located input; not a support judgment");
          line.append(open);
        }
        item.append(line);
      });
    }
    item.append(el("small", `Reference ${o.ref}`));
    box.append(item);
  });
  more.append(box);
}
function renderAtomRows() {
  const box = $("atoms");
  box.hidden = false;
  box.replaceChildren(el("h3", "Atomic source checks"),
    el("p", evidence.atoms.verification_note + " Tick a box only after you have checked that atom against its source."));
  evidence.atoms.rows.forEach((r, i) => {
    const row = el("div"); row.className = "atom-row " + r.evidence_state; row.dataset.atomRow = r.atom_id;
    row.setAttribute("role", "group"); row.setAttribute("aria-label", `Atom ${i + 1}: ${r.text}`);
    const head = el("div"); head.className = "atom-head";
    const field = atomField(r);
    if (field) {
      const label = el("label"); label.className = "atom-check";
      const check = el("input"); check.type = "checkbox"; check.className = "atom-focus";
      check.dataset[field.numeric_span_id ? "quantityField" : "atomField"] = field.field_id;
      check.checked = answers.judgments[field.field_id]?.value === true;
      check.setAttribute("aria-label", `Checked against the source: ${r.text}`);
      check.onchange = () => {
        judgment(field.field_id).value = check.checked;
        document.querySelectorAll(`input[data-quantity-field="${CSS.escape(field.field_id)}"], input[data-atom-field="${CSS.escape(field.field_id)}"]`)
          .forEach((other) => { other.checked = check.checked; });
        save(); progress();
      };
      label.append(check, document.createTextNode(` ${i + 1}. “${r.text}”`));
      head.append(label);
    } else {
      const label = el("span", `${i + 1}. “${r.text}”`); label.className = "atom-focus"; label.tabIndex = 0;
      head.append(label, el("small", "No check control assigned for this atom."));
    }
    const state = el("span", (r.kind === "quantity" ? "Number · " : "Fact · ") + ATOM_STATE[r.evidence_state]);
    state.className = "atom-state"; head.append(state);
    const primary = r.targets[0];
    if (primary) {
      const link = el("button", "Open source: " + describeTarget(primary));
      link.type = "button"; link.className = "atom-link";
      link.setAttribute("aria-label", `Open the source for “${r.text}” at ${describeTarget(primary)}`);
      link.onclick = () => openTarget(primary, link, r);
      head.append(link);
    }
    if (r.reason) head.append(el("small", "Reason: " + r.reason));
    const preparations = r.preparations || [];
    const preparedInputs = r.prepared_inputs || [];
    if (preparations.length || preparedInputs.length) {
      const note = el("small", "Independently prepared evidence is available in the details; it is not a support judgment.");
      note.className = "atom-prepared-note"; head.append(note);
    }
    row.append(head);
    const more = el("details"); more.className = "atom-expand";
    const summaryText = r.calculation || preparations.some((p) => p.calculation)
      ? "Calculation, inputs and sources" : "Evidence details";
    const summary = el("summary", summaryText); summary.className = "atom-summary";
    // Each row's expansion has its own accessible name, distinct from its checkbox and source link.
    summary.setAttribute("aria-label", `${summaryText} for “${r.text}” (atom ${i + 1})`);
    more.append(summary);
    more.ontoggle = () => { if (more.open) logEvent("atom_row_expanded", r.atom_id); };
    (r.author_declarations || []).forEach((d) => renderAuthorDeclaration(d, more));
    if (r.calculation && preparations.length) more.append(el("h4", "Original candidate calculation"));
    if (r.calculation) {
      renderCalculation(r.calculation, more, true, r.calculation.operands, true);
      if (r.calculation_leaves.length) {
        more.append(el("h4", "Input sources"));
        r.calculation_leaves.forEach((leaf, k) => {
          const line = el("p", `Input ${k + 1}: ${leaf.target ? describeTarget(leaf.target) : leaf.source_id} · ${leaf.status}${leaf.reason ? ": " + leaf.reason : ""}`);
          if (leaf.target) {
            const open = el("button", "Open input source");
            open.setAttribute("aria-label", `Open input ${k + 1} source for “${r.text}”`);
            open.onclick = () => openTarget(leaf.target, open, r);
            line.append(open);
          }
          more.append(line);
        });
      }
    }
    renderPreparedInputs(preparedInputs, r, more);
    preparations.forEach((p) => renderPreparation(p, r, more));
    if (r.numeric_span_id) {
      const span = bundle.spans.find((s) => s.span_id === r.numeric_span_id);
      if (span) renderDiagnostics(span, more);
    }
    r.candidates.forEach((t, k) => {
      const line = el("p", `Candidate ${k + 1} of ${r.candidates.length}: ${describeTarget(t)} (not chosen)`);
      const open = el("button", "Open candidate " + (k + 1));
      open.setAttribute("aria-label", `Open candidate ${k + 1} for “${r.text}”`);
      open.onclick = () => openTarget(t, open, r, `Candidate ${k + 1} of ${r.candidates.length}; no source was chosen`);
      line.append(open); more.append(line);
    });
    r.targets.slice(1).forEach((t) => {
      const open = el("button", "Open source: " + describeTarget(t));
      open.onclick = () => openTarget(t, open, r); more.append(open);
    });
    [...r.targets, ...r.candidates].forEach((t) => {
      if (t.binding) more.append(el("small", `${describeTarget(t)}: ${BINDING[t.binding] || t.binding}`));
    });
    if (r.claim_ids.length) more.append(el("small", "Statement ids: " + r.claim_ids.join(", ")));
    more.append(el("small", `Atom ${r.atom_id} · offsets ${r.start}–${r.end} in ${r.field_path}`));
    row.append(more);
    box.append(row);
  });
}
function renderedSource(sourceId) {
  let entry = renderCache.get(sourceId);
  if (!entry) {
    const controller = new AbortController();
    const promise = api("/api/source-render/" + encodeURIComponent(sourceId), undefined, controller.signal);
    entry = { promise, controller };
    renderCache.set(sourceId, entry);
    // Only this request's own entry is dropped on failure, never a newer task's entry.
    promise.catch(() => { if (renderCache.get(sourceId) === entry) renderCache.delete(sourceId); });
  }
  return entry;
}
function buildNode(node, ids) {
  const tags = { line: "span", glyphs: "span", page: "div", notice: "p" };
  const n = document.createElement(tags[node.tag] || node.tag);
  n.dataset.renderId = node.id;
  ids.set(node.id, n);
  if (node.tag === "line") { n.className = "render-line"; n.dataset.line = String(node.line); }
  if (node.tag === "notice") n.className = "render-notice";
  if (node.line && node.tag !== "line") n.dataset.line = String(node.line);
  Object.entries(node.attrs || {}).forEach(([k, v]) => n.setAttribute(k, v));
  // Allowlisted local styling, checked by the server; CSSOM, so the page CSP needs no inline styles.
  Object.entries(node.style || {}).forEach(([k, v]) => n.style.setProperty(k, v));
  if (node.tag === "page") {
    n.className = "pdf-page"; n.dataset.page = String(node.page);
    n.setAttribute("role", "region"); n.setAttribute("aria-label", `Page ${node.page}`);
    n.style.setProperty("width", node.width + "px"); n.style.setProperty("height", node.height + "px");
  }
  if (node.tag === "glyphs") {
    n.className = "pdf-glyphs";
    n.style.setProperty("left", node.x + "px"); n.style.setProperty("top", node.y + "px");
    n.style.setProperty("font-size", node.size + "px");
  }
  if (typeof node.text === "string") n.textContent = node.text;
  (node.children || []).forEach((child) => n.append(buildNode(child, ids)));
  return n;
}
function markExcerpt(node, excerpt) {
  // Non-colour emphasis on the cited words inside an exact node; text is never altered.
  const words = (excerpt || "").replace(/\s+/g, " ").trim();
  if (!words) return;
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const at = t.data.indexOf(words);
    if (at < 0 || t.data.indexOf(words, at + 1) >= 0) continue;
    const range = document.createRange(); range.setStart(t, at); range.setEnd(t, at + words.length);
    const mark = el("mark"); mark.className = "cited-words"; range.surroundContents(mark);
    return;
  }
}
const NOTE_TEXT = /^\s*(?:\*|†|‡|§|¹|²|³|\(\w{1,3}\)|\[\w{1,3}\]|\d{1,2}[.)]\s|(?:foot)?notes?\b|source\b|n\/?m\b)/i;
function plainText(node) {
  return (node?.textContent || "").replace(/\s+/g, " ").trim();
}
function tableGrid(table) {
  // Occupied-cell grid of this table only (table.rows never includes a nested table's rows): rowSpan and
  // colSpan are honoured across rows; a rowspan ends at its row group (thead, each tbody, tfoot), and
  // rowSpan 0 runs to the end of that group. A malformed overlap keeps the first occupant, deterministically.
  const rows = [...table.rows];
  const grid = rows.map(() => []);
  const at = new Map();
  rows.forEach((tr, r) => {
    let column = 0;
    for (const cell of tr.cells) {
      while (grid[r][column]) column += 1;
      const colSpan = Math.max(1, cell.colSpan || 1);
      let end = r + 1;
      while (end < rows.length && rows[end].parentNode === tr.parentNode
        && (cell.rowSpan === 0 || end < r + Math.max(1, cell.rowSpan || 1))) end += 1;
      at.set(cell, { row: r, col: column, rows: end - r, cols: colSpan });
      for (let y = r; y < end; y += 1) for (let x = column; x < column + colSpan; x += 1) grid[y][x] ||= cell;
      column += colSpan;
    }
  });
  return { rows, grid, at };
}
const UNAVAILABLE = "unavailable";
function headerNames(cells) {
  const names = cells.map(plainText);
  return names.length && names.every(Boolean) ? names.join(" / ") : UNAVAILABLE;
}
function cellHeaders(cell, table, heads) {
  // Column and row headers of one cell: explicit headers/id first, then scope, then grid position.
  // Each is a " / "-joined name, or "unavailable" when the association is missing or ambiguous.
  const { rows, grid, at } = tableGrid(table);
  const pos = at.get(cell);
  if (!pos) return { column: UNAVAILABLE, row: UNAVAILABLE };
  const before = (a, b) => (at.get(a).row - at.get(b).row) || (at.get(a).col - at.get(b).col);
  const explicit = (cell.dataset.headers || "").split(" ").filter(Boolean);
  if (explicit.length) {
    const byId = new Map();
    for (const c of at.keys()) {
      const id = c.dataset.cellId;
      if (id) byId.set(id, byId.has(id) ? null : c); // A duplicated id resolves to nothing.
    }
    const found = explicit.map((id) => byId.get(id));
    if (found.some((c) => !c)) return { column: UNAVAILABLE, row: UNAVAILABLE };
    // A declared scope decides first: row/rowgroup heads the cell's row, col/colgroup its column (so a
    // rowgroup header above the cell is still a row header). Only without a valid scope does geometry
    // decide: a header whose rows overlap the cell's heads its row; one above (or below) it heads its column.
    const overlaps = (c) => at.get(c).row < pos.row + pos.rows && pos.row < at.get(c).row + at.get(c).rows;
    const isRowHead = (c) => {
      const declared = (c.getAttribute("scope") || "").toLowerCase();
      if (declared === "row" || declared === "rowgroup") return true;
      if (declared === "col" || declared === "colgroup") return false;
      return overlaps(c);
    };
    const colHeads = found.filter((c) => !isRowHead(c)).sort(before);
    const rowHeads = found.filter(isRowHead).sort(before);
    return { column: colHeads.length ? headerNames(colHeads) : UNAVAILABLE,
      row: rowHeads.length ? headerNames(rowHeads) : UNAVAILABLE };
  }
  const scope = (c) => (c.getAttribute("scope") || "").toLowerCase();
  const isHeadRow = (r) => heads.includes(rows[r]);
  // One header level per grid row above the cell; two different header cells at one level is ambiguous.
  let ambiguous = false;
  const colHeads = [];
  for (let r = 0; r < pos.row; r += 1) {
    const level = new Set();
    for (let x = pos.col; x < pos.col + pos.cols; x += 1) {
      const c = grid[r][x];
      if (!c || c.tagName !== "TH" || /^row/.test(scope(c))) continue;
      if (/^col/.test(scope(c)) || isHeadRow(at.get(c).row)) level.add(c);
    }
    if (level.size > 1) ambiguous = true;
    level.forEach((c) => { if (!colHeads.includes(c)) colHeads.push(c); });
  }
  // One header level per grid column left of the cell, in the cell's own rows; rowgroup headers above
  // in the same row group also apply.
  const rowHeads = [];
  let rowAmbiguous = false;
  if (!isHeadRow(pos.row)) {
    for (let x = 0; x < pos.col; x += 1) {
      const level = new Set();
      for (let y = 0; y < pos.row + pos.rows; y += 1) {
        const c = grid[y][x];
        if (!c || c.tagName !== "TH" || /^col/.test(scope(c)) || isHeadRow(at.get(c).row)) continue;
        const own = y >= pos.row;
        const group = scope(c) === "rowgroup" && rows[at.get(c).row].parentNode === rows[pos.row].parentNode;
        if (own || group) level.add(c);
      }
      if (level.size > 1) rowAmbiguous = true;
      level.forEach((c) => { if (!rowHeads.includes(c)) rowHeads.push(c); });
    }
  }
  return {
    column: ambiguous || !colHeads.length ? UNAVAILABLE : headerNames(colHeads.sort(before)),
    row: rowAmbiguous || !rowHeads.length ? UNAVAILABLE : headerNames(rowHeads.sort(before)),
  };
}
function targetContext(hit, doc) {
  // The table context a long document scrolls away from the hit: its column header (period, unit),
  // row label (entity, metric), caption, table notes and footnotes, read verbatim from the rendering.
  const parts = [];
  const heading = [...doc.querySelectorAll("h1, h2, h3, h4, h5, h6")]
    .filter((h) => h.compareDocumentPosition(hit) & Node.DOCUMENT_POSITION_FOLLOWING).pop();
  const table = hit.closest("table");
  if (!table || !doc.contains(table)) {
    if (heading && !heading.contains(hit)) parts.push(["Section", plainText(heading)]);
    return parts;
  }
  // Only this table's own row and cell: a hit inside a nested table never reads the outer table's grid.
  const tr = [hit.closest("tr")].find((r) => r && r.closest("table") === table) || null;
  const cell = [hit.closest("td, th")].find((c) => c && c.closest("table") === table) || null;
  const firstRow = table.rows[0];
  const heads = table.tHead ? [...table.tHead.rows]
    : [firstRow].filter((r) => r && r !== tr && [...r.cells].every((c) => c.tagName === "TH"));
  if (cell && tr && !heads.includes(tr)) {
    const { column, row } = cellHeaders(cell, table, heads);
    parts.push(["Column", column]);
    // A row header cell is its own row label; it has no row header of its own to announce.
    if (!(cell.tagName === "TH" && row === UNAVAILABLE)) parts.push(["Row", row]);
  } else if (tr && !heads.includes(tr) && tr.cells[0]?.tagName === "TH") parts.push(["Row", plainText(tr.cells[0])]);
  if (table.caption) parts.push(["Caption", plainText(table.caption)]);
  else {
    const before = table.previousElementSibling;
    if (before && !/^H[1-6]$/.test(before.tagName) && plainText(before).length <= 200) parts.push(["Above the table", plainText(before)]);
  }
  if (heading) parts.push(["Section", plainText(heading)]);
  if (table.tFoot) parts.push(["Table note", plainText(table.tFoot)]);
  let next = table.nextElementSibling;
  for (let k = 0; next && k < 6; k += 1, next = next.nextElementSibling) {
    const text = plainText(next);
    if (!text) continue;
    if (!NOTE_TEXT.test(text)) break;
    parts.push(["Note", text]);
  }
  return parts.filter(([, v]) => v);
}
async function openTarget(target, origin, row, note = "") {
  const viewer = $("viewer");
  const generation = ++navigation, epoch = taskEpoch, bundleId = bundle.bundle_id, bundleHash = state.bundle_hash;
  const stale = () => generation !== navigation || epoch !== taskEpoch || bundleId !== bundle.bundle_id
    || bundleHash !== state.bundle_hash;
  // A pending open of another source is superseded: abort it (best effort; the fence still holds).
  if (inflight && inflight.sourceId !== target.source_id) {
    try { inflight.controller.abort(); } catch (_) { /* fenced regardless */ }
    if (renderCache.get(inflight.sourceId) === inflight.entry) renderCache.delete(inflight.sourceId);
  }
  inflight = null;
  viewerOrigin = origin;
  viewer.hidden = false;
  viewer.dataset.targetId = target.target_id;
  logEvent("rendered_target_opened", target.target_id);
  if (!target.rendered) {
    // No rendering for this citation: show the frozen lines rather than nothing.
    const source = bundle.sources.find((s) => s.source_id === target.source_id);
    viewer.replaceChildren(el("p", target.reason || "No rendered source for this citation."));
    if (source) { $("evidence").replaceChildren(); showSource(source, target.start_line || 1, target.end_line || target.start_line || 1); }
    return;
  }
  viewer.replaceChildren(el("p", "Opening source…"));
  const entry = renderedSource(target.source_id);
  inflight = { sourceId: target.source_id, controller: entry.controller, entry };
  let data;
  try { data = await entry.promise; }
  catch (err) {
    if (stale()) return;
    inflight = null;
    viewer.replaceChildren(el("p", "Rendered source unavailable: " + err.message));
    return;
  }
  if (stale()) return;
  inflight = null;
  if (data.manifest.bundle_hash !== bundleHash) {
    viewer.replaceChildren(el("p", "Rendered source unavailable: it belongs to a different task."));
    return;
  }
  const m = data.manifest;
  const record = m.targets.find((t) => t.target_id === target.target_id) || target;
  const source = bundle.sources.find((s) => s.source_id === m.source_id);
  const header = el("div"); header.className = "viewer-head";
  const back = el("button", "Back to row"); back.type = "button";
  back.onclick = () => origin?.focus();
  header.append(el("h3", source ? source.title : m.source_id), back);
  const label = {exact: "Exact location highlighted", page_only: "Page-only location: the line is not highlighted",
    ambiguous: "Ambiguous: several matches, none chosen", unavailable: "Location unavailable"}[record.status] || record.status;
  const statusLine = el("p", `${label}${record.reason ? " · " + record.reason : ""}${note ? " · " + note : ""}`);
  statusLine.className = "target-status"; statusLine.setAttribute("role", "status");
  const fidelity = el("details"); fidelity.className = "render-fidelity";
  fidelity.append(el("summary", `Rendering: ${m.render_mode.replaceAll("_", " ")} · ${m.lineage.replaceAll("_", " ")}`),
    el("p", m.fidelity_note), el("small", `Frozen SHA-256 ${m.frozen_sha256}`),
    el("small", m.raw_sha256 ? `Original SHA-256 ${m.raw_sha256} (${m.raw_media_type})` : "Original bytes not supplied"),
    el("small", `Derivative SHA-256 ${m.derivative_sha256}`));
  (m.transforms || []).forEach((t) => fidelity.append(el("small", "Transform: " + t)));
  const doc = el("div"); doc.className = "rendered-doc " + m.render_mode; doc.setAttribute("aria-label", "Rendered source");
  const ids = new Map();
  data.derivative.nodes.forEach((n) => doc.append(buildNode(n, ids)));
  const context = el("p"); context.className = "target-context"; context.id = "target-context"; context.hidden = true;
  viewer.replaceChildren(header, statusLine, context, fidelity, doc);
  let first = null;
  if (record.status === "exact") {
    const found = record.dom_targets.map((id) => ids.get(id));
    if (!found.length || found.some((n) => !n)) {
      // Never claim an exact location the page cannot show (the server refuses such manifests first).
      statusLine.textContent = "Location unavailable · the exact target is missing from this rendering; nothing is highlighted"
        + (note ? " · " + note : "");
      statusLine.dataset.downgraded = "missing-node";
      statusLine.tabIndex = -1;
    } else found.forEach((n) => {
      n.classList.add("target-hit"); n.setAttribute("aria-current", "location");
      if (n.tagName !== "TR") markExcerpt(n, record.excerpt);
      first = first || n;
    });
    if (first) {
      const parts = targetContext(first, doc);
      if (parts.length) {
        context.textContent = "Context of the cited location: " + parts.map(([k, v]) => `${k}: ${v}`).join(" · ");
        context.hidden = false;
        first.setAttribute("aria-describedby", "target-context");
      }
    }
  } else if (record.status === "page_only" && record.page) {
    first = doc.querySelector(`[data-page="${record.page}"]`);
    first?.classList.add("target-page");
  } else if (record.status === "ambiguous") {
    record.candidates.forEach((ids_, k) => ids_.forEach((id) => {
      const n = ids.get(id); if (!n) return;
      n.classList.add("target-candidate"); n.dataset.candidate = String(k + 1); first = first || n;
    }));
  }
  if (first) {
    const flag = el("span", record.status === "exact" ? "▶ cited" : record.status === "page_only" ? "▶ cited page" : "? candidate");
    flag.className = "target-flag"; flag.setAttribute("aria-hidden", "true");
    first.prepend(flag);
    first.tabIndex = -1;
    first.scrollIntoView({ block: "center" });
    first.focus({ preventScroll: true });
  } else statusLine.focus?.();
}
function renderAtomic() {
  document.body.classList.add("atomic-mode");
  document.querySelector('[aria-label="Judgments"] > h2').textContent = "Atomic checks and judgment";
  document.querySelector('[aria-label="Evidence"] > h2').textContent = "Rendered source";
  renderAtomicStatements();
  renderAtomRows();
  $("viewer").hidden = false;
  $("evidence-panel").open = false;
  if (!$("viewer").childElementCount) $("viewer").append(el("p", "Select “Open source” on a row to show its exact location here."));
}
$("viewer").addEventListener("keydown", (event) => {
  if (event.key === "Escape" && viewerOrigin) { event.preventDefault(); viewerOrigin.focus(); }
});
function setupPaneDividers() {
  const main = document.querySelector("main");
  const prefs = { left: 52.4, top: 35 };
  const storageKey = "evidence-review-pane-sizes/v1";
  const layoutStatus = el("span"); layoutStatus.setAttribute("role", "status");
  document.querySelector("header").append(layoutStatus);
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
      prefs.left = Math.max(15, Math.min(85, saved.left));
      prefs.top = Math.max(15, Math.min(85, saved.top));
    }
  } catch (error) {
    // Preferences are optional; judgments remain durable. Report storage failure.
    layoutStatus.textContent = "Pane-size preference unavailable; using default layout.";
  }
  const apply = () => {
    main.style.setProperty("--left-pane", prefs.left + "fr");
    main.style.setProperty("--right-pane", (100 - prefs.left) + "fr");
    main.style.setProperty("--top-pane", prefs.top + "fr");
    main.style.setProperty("--bottom-pane", (100 - prefs.top) + "fr");
  };
  const persist = () => {
    try { localStorage.setItem(storageKey, JSON.stringify(prefs)); }
    catch (error) {
      // Layout still works in this tab; expose missing reload persistence.
      layoutStatus.textContent = "Pane sizes changed for this tab; browser could not save the layout.";
    }
  };
  [["pane-height-divider", "top", "y"], ["pane-width-divider", "left", "x"]].forEach(([id, key, axis]) => {
    const divider = $(id);
    let dragging = false;
    const update = (value) => {
      const bounds = main.getBoundingClientRect();
      const style = getComputedStyle(main);
      const available = (axis === "x" ? bounds.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
        : bounds.height - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)) - 10;
      const limit = Math.min(45, Math.max(15, 100 * (axis === "x" ? 200 : 140) / available));
      prefs[key] = Math.max(limit, Math.min(100 - limit, value));
      divider.setAttribute("aria-valuemin", String(Math.round(limit)));
      divider.setAttribute("aria-valuemax", String(Math.round(100 - limit)));
      divider.setAttribute("aria-valuenow", String(Math.round(prefs[key])));
      divider.setAttribute("aria-valuetext", Math.round(prefs[key]) + " percent");
      apply();
    };
    divider.onpointerdown = (event) => {
      if (event.button !== 0) return;
      dragging = true; divider.setPointerCapture(event.pointerId);
      document.body.classList.add("resizing-panes"); event.preventDefault();
    };
    divider.onpointermove = (event) => {
      if (!dragging) return;
      const bounds = main.getBoundingClientRect(); const style = getComputedStyle(main);
      const start = axis === "x" ? bounds.left + parseFloat(style.paddingLeft) : bounds.top + parseFloat(style.paddingTop);
      const available = (axis === "x" ? bounds.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
        : bounds.height - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)) - 10;
      update(100 * ((axis === "x" ? event.clientX : event.clientY) - start - 5) / available);
    };
    const stop = () => {
      if (!dragging) return;
      dragging = false; document.body.classList.remove("resizing-panes"); persist();
    };
    divider.onpointerup = stop; divider.onlostpointercapture = stop;
    divider.onkeydown = (event) => {
      const decrease = axis === "x" ? "ArrowLeft" : "ArrowUp";
      const increase = axis === "x" ? "ArrowRight" : "ArrowDown";
      if (event.key !== decrease && event.key !== increase) return;
      event.preventDefault(); update(prefs[key] + (event.key === decrease ? -1 : 1) * (event.shiftKey ? 10 : 2)); persist();
    };
    divider.ondblclick = () => { update(key === "left" ? 52.4 : 35); persist(); };
    divider.title = "Drag to resize; arrow keys adjust; double-click restores default";
    update(prefs[key]);
  });
  apply();
}
setupPaneDividers();
// Without a launch token nothing is requested; the reviewer gets a safe way back in.
function showRecovery(reason) {
  $("status").className = "save-error";
  $("status").textContent = reason + " Reopen this review from the launch link printed by the review command; "
    + "no answers are lost, because saved judgments live on the review server, not in this tab.";
}
if (!token) {
  showRecovery(tabStore.available ? "This page was opened without its launch token."
    : "This browser blocks tab storage, so the launch token could not be kept across a reload.");
} else {
  load().catch((err) => {
    if (err.status === 401 || err.status === 403) {
      showRecovery("The review server did not accept this tab's launch token.");
      return;
    }
    $("status").className = "save-error";
    $("status").textContent = err.message;
  });
}
