(function () {
  "use strict";

  const state = {
    view: "overview",
    selectedId: null,
    overview: null,
    queue: [],
    detail: null,
    calls: [],
    dismissedCalls: {},
    injected: false,
  };

  const overviewBoard = document.getElementById("overview-board");
  const queueBoard = document.getElementById("queue-board");
  const detailBoard = document.getElementById("detail-board");
  const evidenceBoard = document.getElementById("evidence-board");
  const judgeStatus = document.getElementById("judge-status");
  const judgeTrigger = document.getElementById("judge-trigger");
  const judgeLabel = document.getElementById("judge-label");
  const incoming = document.getElementById("incoming-call");
  const incomingCopy = document.getElementById("incoming-copy");

  function $(id) {
    return document.getElementById(id);
  }

  function fmt(value) {
    if (value === null || value === undefined || value === "") return "not in store";
    if (typeof value === "number") {
      if (value <= 1 && value >= 0) return (value * 100).toFixed(1).replace(/\.0$/, "") + "%";
      return String(value);
    }
    if (typeof value === "object") {
      if ("amount" in value) {
        const amount = value.amount;
        const currency = value.currency || "";
        return currency ? currency + " " + amount : String(amount);
      }
      return JSON.stringify(value);
    }
    return String(value);
  }

  function money(value) {
    if (!value || typeof value !== "object" || !("amount" in value)) return "not in store";
    const currency = value.currency || "";
    return (currency ? currency + " " : "") + Number(value.amount).toLocaleString("en-US");
  }

  function ratio(value) {
    if (typeof value !== "number") return "not in store";
    return (value * 100).toFixed(1) + "%";
  }

  function cohortLine(cohort) {
    if (!cohort || typeof cohort !== "object") return "cohort not in store";
    const order = ["merchant_id", "provider", "payment_method", "card_network", "country", "issuing_bank"];
    return order
      .filter(function (key) { return cohort[key]; })
      .map(function (key) { return cohort[key]; })
      .join(" · ");
  }

  function incidentScope(payload) {
    payload = payload || {};
    if (payload.merchant_id) return "Merchant " + payload.merchant_id;
    if (payload.scope_label) return payload.scope_label;
    const line = cohortLine(payload.affected_cohort);
    if (line && line !== "cohort not in store") return line;
    return "Platform-wide";
  }

  function confidenceLabel(confidence, lifecycle) {
    if (confidence) return String(confidence);
    if (lifecycle === "investigating") return "awaiting investigation";
    return "not in store";
  }

  function isInvestigating(record) {
    return ((record && record.lifecycle_state) || "") === "investigating";
  }

  function narrativePlaceholder(incident, investigation) {
    if (isInvestigating(incident)) {
      return "Investigation is running. This usually takes about a minute.";
    }
    const outcome = investigation && investigation.outcome;
    if (outcome === "agent_unavailable") {
      return "Narrative unavailable because the investigation agent failed.";
    }
    if (outcome) {
      return "Narrative unavailable (" + outcome + ").";
    }
    return "Investigation has not run yet.";
  }

  function statusBanner(incident, investigation) {
    if (isInvestigating(incident)) {
      return '<p class="banner">Investigation is running. This usually takes about a minute.</p>';
    }
    if (investigation && investigation.narrative_available) return "";
    const outcome = (investigation && investigation.outcome) || "no investigation";
    return '<p class="banner">Narrative unavailable (' +
      escapeHtml(outcome) +
      "). Localisation, money and the evidence trail remain.</p>";
  }

  function badgePair(severity, confidence, lifecycle) {
    const sev = document.createElement("span");
    sev.className = "severity";
    sev.setAttribute("data-severity", severity || "unknown");
    sev.textContent = "severity " + (severity || "unknown");
    const conf = document.createElement("span");
    const confValue = confidenceLabel(confidence, lifecycle);
    conf.className = "confidence";
    conf.setAttribute("data-confidence", confidence || confValue);
    conf.textContent = "confidence " + confValue;
    const wrap = document.createElement("div");
    wrap.className = "badges";
    wrap.appendChild(sev);
    wrap.appendChild(conf);
    return wrap;
  }

  function setView(name) {
    state.view = name;
    document.querySelectorAll(".rail-btn").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-view") === name);
    });
    document.querySelectorAll(".view").forEach(function (section) {
      const active = section.getAttribute("data-view") === name;
      section.classList.toggle("is-active", active);
      section.hidden = !active;
    });
  }

  function renderOverview() {
    const data = state.overview;
    if (!data) {
      overviewBoard.innerHTML = '<p class="empty">No overview yet.</p>';
      return;
    }
    const merchants = (data.merchant_health || []).map(function (row) {
      return (
        '<article class="merchant-card">' +
        "<h3>" + escapeHtml(row.scope_label || row.merchant_id || "Platform-wide") + "</h3>" +
        '<p>Highest stored severity: <span class="severity" data-severity="' +
        escapeHtml(row.highest_severity || "unknown") + '">severity ' +
        escapeHtml(row.highest_severity || "unknown") + "</span></p>" +
        "<p>Active incidents: " + escapeHtml(String(row.active_incident_count)) + "</p>" +
        "</article>"
      );
    }).join("");
    overviewBoard.innerHTML =
      '<dl class="metrics">' +
      metric("Current conversion", ratio(data.current_conversion), "from incident " + (data.source_incident_id || "none")) +
      metric("Expected conversion", ratio(data.expected_conversion), "copied from the same record") +
      metric("GMV", money(data.gmv), "attempted_value on that incident") +
      metric("GMV at risk", money(data.estimated_gmv_at_risk), "gmv_at_risk on that incident") +
      metric("Active incidents", String(data.active_incident_count), "count of stored records") +
      "</dl>" +
      "<h3>Merchant health</h3>" +
      '<div class="merchants">' + (merchants || '<p class="empty">No merchant incidents in the store.</p>') + "</div>";
  }

  function metric(label, value, hint) {
    return (
      '<div class="metric"><dt>' + escapeHtml(label) + "</dt><dd>" +
      escapeHtml(value) + '<span class="sub">' + escapeHtml(hint) + "</span></dd></div>"
    );
  }

  function renderQueue() {
    if (!state.queue.length) {
      queueBoard.innerHTML = '<p class="empty">No incidents in the store.</p>';
      return;
    }
    queueBoard.innerHTML = "";
    state.queue.forEach(function (item) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "queue-row";
      row.appendChild(badgePair(item.severity, item.diagnostic_confidence, item.lifecycle_state));
      const mid = document.createElement("div");
      mid.innerHTML =
        "<strong>" + escapeHtml(item.incident_id || "") + "</strong>" +
        '<div class="cohort">' + escapeHtml(cohortLine(item.affected_cohort)) + "</div>";
      const open = document.createElement("span");
      open.className = "q-open";
      open.textContent = "Open";
      row.appendChild(mid);
      row.appendChild(open);
      row.addEventListener("click", function () {
        state.selectedId = item.incident_id;
        loadDetail(item.incident_id).then(function () {
          setView("detail");
        });
      });
      queueBoard.appendChild(row);
    });
  }

  function renderDetail() {
    const detail = state.detail;
    if (!detail) {
      detailBoard.innerHTML = '<p class="empty">Select an incident from the queue.</p>';
      return;
    }
    const incident = detail.incident || {};
    const investigation = detail.investigation || {};
    const questions = detail.questions || {};
    const banner = statusBanner(incident, investigation);
    const channels = (detail.escalation || []).map(function (event) {
      return escapeHtml(event.channel) + ": " + escapeHtml(event.status);
    }).join(" · ");
    detailBoard.innerHTML = "";
    const head = document.createElement("div");
    head.appendChild(badgePair(
      incident.severity,
      (investigation.result || {}).diagnostic_confidence,
      incident.lifecycle_state
    ));
    const meta = document.createElement("p");
    meta.className = "cohort";
    const outcome = investigation.outcome;
    meta.textContent = (incident.incident_id || "") + " · " + (incident.lifecycle_state || "") +
      (outcome ? " · " + outcome : "") +
      (channels ? " · " + channels : "");
    detailBoard.insertAdjacentHTML("beforeend", banner);
    detailBoard.appendChild(head);
    detailBoard.appendChild(meta);
    const grid = document.createElement("div");
    grid.className = "questions";
    grid.appendChild(questionCard("1. What changed?", questions.what_changed));
    grid.appendChild(questionCard("2. Where?", questions.where));
    grid.appendChild(questionCard("3. How much does it matter?", questions.how_much_it_matters));
    const narrativeBody = questions.narrative_available
      ? null
      : narrativePlaceholder(incident, investigation);
    grid.appendChild(questionCard(
      "4. What probably caused it?",
      questions.narrative_available ? questions.what_probably_caused_it : narrativeBody
    ));
    grid.appendChild(questionCard(
      "5. Why do we believe that?",
      questions.narrative_available ? questions.why_we_believe_that : narrativeBody
    ));
    grid.appendChild(questionCard(
      "6. What should the TAM do?",
      questions.narrative_available ? questions.what_the_operator_should_do : narrativeBody
    ));
    detailBoard.appendChild(grid);
  }

  function questionCard(title, body) {
    const article = document.createElement("article");
    article.className = "question";
    const heading = document.createElement("h3");
    heading.textContent = title;
    const pre = document.createElement("pre");
    pre.textContent = pretty(body);
    article.appendChild(heading);
    article.appendChild(pre);
    return article;
  }

  function renderEvidence() {
    const detail = state.detail;
    if (!detail) {
      evidenceBoard.innerHTML = '<p class="empty">Select an incident to inspect its evidence trail.</p>';
      return;
    }
    const incident = detail.incident || {};
    const investigation = detail.investigation || {};
    const trail = detail.evidence_trail || [];
    const running = isInvestigating(incident);
    const banner = running
      ? '<p class="banner">Investigation is running. This usually takes about a minute.</p>'
      : (investigation.narrative_available
        ? ""
        : '<p class="banner">Narrative unavailable. The trail still shows every query that ran.</p>');
    if (!trail.length) {
      const empty = running
        ? '<p class="empty">Investigation is running. The evidence trail is stored when it finishes.</p>'
        : '<p class="empty">No evidence trail is stored for this incident.</p>';
      evidenceBoard.innerHTML = banner + empty;
      return;
    }
    evidenceBoard.innerHTML = banner;
    trail.forEach(function (entry) {
      const card = document.createElement("article");
      card.className = "trail-card";
      card.innerHTML =
        "<h3>Query " + escapeHtml(String(entry.sequence)) + " · " + escapeHtml(entry.tool || "") + "</h3>" +
        '<p class="cohort">' + escapeHtml(entry.query_id || "") + " · " + escapeHtml(entry.timestamp || "") +
        " · " + escapeHtml(entry.outcome || "") + "</p>" +
        '<div class="asked"><strong>Asked</strong><pre>' + escapeHtml(pretty(entry.parameters)) + "</pre></div>" +
        '<div class="returned"><strong>Returned</strong><pre>' + escapeHtml(pretty(entry.response)) + "</pre></div>";
      evidenceBoard.appendChild(card);
    });
  }

  function pretty(value) {
    if (value === null || value === undefined) return "not in store";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch (err) {
      return String(value);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function jsonGet(path) {
    return fetch(path, { cache: "no-store" }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw body;
        return body;
      });
    });
  }

  function refresh() {
    return jsonGet("/api/overview").then(function (overview) {
      state.overview = overview;
      state.queue = overview.incidents || [];
      if (!state.selectedId && state.queue.length) state.selectedId = state.queue[0].incident_id;
      renderOverview();
      renderQueue();
      return jsonGet("/api/calls");
    }).then(function (payload) {
      state.calls = payload.calls || [];
      showCall(state.calls[0] || null);
      if (state.selectedId) return loadDetail(state.selectedId);
    }).catch(function (err) {
      judgeStatus.textContent = "Store read failed. " + (err && err.error ? err.error : "Retrying.");
    });
  }

  function loadDetail(incidentId) {
    return jsonGet("/api/incidents/" + encodeURIComponent(incidentId)).then(function (detail) {
      state.detail = detail;
      renderDetail();
      renderEvidence();
    });
  }

  function showCall(call) {
    if (!call || state.dismissedCalls[call.incident_id]) {
      incoming.hidden = true;
      return;
    }
    const payload = call.payload || {};
    const action = payload.recommended_next_action || {};
    incomingCopy.textContent = [
      "Incident " + (call.incident_id || ""),
      "Severity " + (payload.severity || "unknown") + " (from the incident record)",
      incidentScope(payload),
      action.action || "Recommended action not in store",
    ].join("\n");
    incoming.dataset.incidentId = call.incident_id;
    incoming.hidden = false;
  }

  document.querySelectorAll(".rail-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setView(btn.getAttribute("data-view"));
    });
  });

  // The judge control is a toggle over one named target, not a scenario
  // picker: the target is decided in surfaces/inject.py and reported back in
  // every response, so the UI names it rather than choosing it.
  function targetLabel(payload) {
    const target = (payload && payload.target) || {};
    if (!target.merchant_id) return "one live merchant";
    return target.effect + " on provider " + target.provider + " for " + target.merchant_id;
  }

  function renderJudge(payload) {
    const active = !!(payload && payload.active);
    state.injected = active;
    judgeTrigger.setAttribute("data-on", active ? "true" : "false");
    judgeTrigger.setAttribute("aria-pressed", active ? "true" : "false");
    judgeLabel.textContent = active ? "Stop hidden incident" : "Fire hidden incident";
  }

  function loadJudgeState() {
    return jsonGet("/api/trigger").then(function (payload) {
      renderJudge(payload);
      judgeStatus.textContent = state.injected
        ? "An incident is injected: " + targetLabel(payload) + ". Toggle off to clear it."
        : "Judge trigger ready: " + targetLabel(payload) + ". The scenario stays hidden from detection.";
    }).catch(function () {
      judgeStatus.textContent = "The judge control could not reach its own server. Nothing is injected.";
    });
  }

  $("judge-form").addEventListener("submit", function (event) {
    event.preventDefault();
    const wanted = !state.injected;
    judgeTrigger.disabled = true;
    judgeStatus.textContent = wanted
      ? "Publishing the incident to W1…"
      : "Publishing the stop command to W1…";
    fetch("/api/trigger", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: wanted }),
    })
      .then(function (response) { return response.json(); })
      .then(function (body) {
        renderJudge(body);
        // body.message is the server's own account of what happened, including
        // the unreachable-broker case. The UI never upgrades a failure into a
        // claim that a scenario fired.
        judgeStatus.textContent = body.message || "The trigger returned no account of what it did.";
        refresh();
      })
      .catch(function () {
        judgeStatus.textContent = "The judge control could not reach its own server. Nothing was injected.";
      })
      .then(function () {
        judgeTrigger.disabled = false;
      });
  });

  $("answer-call").addEventListener("click", function () {
    const incidentId = incoming.dataset.incidentId;
    incoming.hidden = true;
    if (!incidentId) {
      return;
    }
    state.dismissedCalls[incidentId] = true;
    fetch("/api/calls/" + encodeURIComponent(incidentId) + "/ack", { method: "POST", cache: "no-store" })
      .then(function () {
        incoming.hidden = true;
        refresh();
      });
  });

  function tick() {
    const now = new Date();
    $("clock").textContent = now.toISOString().replace(".000Z", "Z");
  }

  tick();
  setInterval(tick, 1000);
  loadJudgeState();
  refresh();
  setInterval(refresh, 2500);
})();
