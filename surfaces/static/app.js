(function () {
  "use strict";

  const state = {
    view: "overview",
    selectedId: null,
    overview: null,
    queue: [],
    merchants: [],
    escalations: null,
    calls: [],
    detail: null,
    injected: false,
  };

  const overviewBoard = document.getElementById("overview-board");
  const queueBoard = document.getElementById("queue-board");
  const detailBoard = document.getElementById("detail-board");
  const escalationBoard = document.getElementById("escalation-board");
  const evidenceBoard = document.getElementById("evidence-board");
  const judgeStatus = document.getElementById("judge-status");
  const judgeTrigger = document.getElementById("judge-trigger");
  const judgeLabel = document.getElementById("judge-label");
  const provSource = document.getElementById("prov-source");
  const queueWho = document.getElementById("queue-who");
  const drawer = document.getElementById("drawer");
  const drawerTitle = document.getElementById("drawer-title");
  const drawerLede = document.getElementById("drawer-lede");
  const drawerBody = document.getElementById("drawer-body");
  const scrim = document.getElementById("scrim");

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

  // Counts are never ratios. fmt() reads a bare 1 as 100%, which would put a
  // percentage on screen that exists nowhere in the store.
  function count(value) {
    if (value === null || value === undefined || value === "") return "not in store";
    if (typeof value === "number") return String(value);
    return String(value);
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
      return '<div class="note warn tight banner"><h4>Investigation is running</h4><p>This usually takes about a minute.</p></div>';
    }
    if (investigation && investigation.narrative_available) return "";
    const outcome = (investigation && investigation.outcome) || "no investigation";
    return '<div class="note warn tight banner"><h4>Narrative unavailable (' +
      escapeHtml(outcome) +
      ")</h4><p>Localisation, money and the evidence trail remain.</p></div>";
  }

  function severityClass(severity) {
    const value = String(severity || "").toLowerCase();
    if (value === "critical" || value === "high" || value === "medium" || value === "low") {
      return "sev-" + value;
    }
    return "sev-low";
  }

  function kpiKind(severity) {
    const value = String(severity || "").toLowerCase();
    if (value === "critical") return "red";
    if (value === "high") return "amber";
    return "grey";
  }

  function lampHtml(kind) {
    if (kind === "red") return '<span class="lamp"><i class="on"></i><i></i><i></i></span>';
    if (kind === "amber") return '<span class="lamp"><i></i><i class="on"></i><i></i></span>';
    return '<span class="lamp"><i></i><i></i><i class="on grey"></i></span>';
  }

  function badgePair(severity, confidence, lifecycle) {
    const sev = document.createElement("span");
    const sevValue = severity || "unknown";
    sev.className = "sev " + severityClass(sevValue);
    sev.setAttribute("data-severity", sevValue);
    const mark = document.createElement("i");
    sev.appendChild(mark);
    sev.appendChild(document.createTextNode(String(sevValue)));
    const conf = document.createElement("span");
    const confValue = confidenceLabel(confidence, lifecycle);
    conf.className = "conf" + (confidence ? "" : " none");
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
    document.querySelectorAll(".jump-link").forEach(function (btn) {
      btn.classList.toggle("on", btn.getAttribute("data-view") === name);
    });
    document.querySelectorAll(".view").forEach(function (section) {
      const active = section.getAttribute("data-view") === name;
      section.classList.toggle("is-active", active);
      section.hidden = !active;
    });
  }

  function citeButton(citeId, label) {
    return '<button type="button" class="cite" data-cite="' +
      escapeHtml(citeId) +
      '" aria-label="' +
      escapeHtml(label || "cite") +
      '"></button>';
  }

  // The cite dot rides on the hint line, not the headline value: a long money
  // figure plus a trailing dot wraps the dot onto a line of its own.
  function kpi(kind, label, value, hint, citeId) {
    const foot = citeId
      ? '<span class="fig">' + escapeHtml(hint) + citeButton(citeId, "cite " + label) + "</span>"
      : escapeHtml(hint);
    return (
      '<div class="kpi kpi-' + kind + '">' +
      lampHtml(kind) +
      "<dt>" + escapeHtml(label) + "</dt><dd>" + escapeHtml(value) + "</dd>" +
      "<p>" + foot + "</p></div>"
    );
  }

  function renderOverview() {
    const data = state.overview;
    if (!data) {
      overviewBoard.innerHTML = '<p class="empty">No overview yet.</p>';
      provSource.innerHTML = "<b>source</b> waiting for store";
      return;
    }
    const headline = (data.incidents || [])[0] || null;
    const sourceId = data.source_incident_id || "none";
    provSource.innerHTML = "<b>source</b> " + escapeHtml(sourceId);
    if (!headline) {
      overviewBoard.innerHTML =
        '<p class="empty">No incidents in the store.</p>' +
        '<div class="note warn tight"><h4>Two things this header deliberately does not show</h4>' +
        "<p><b>A portfolio total.</b> Adding cited <code>loss_per_hour</code> figures would be a number that exists only here. A real total has to come from W2 as its own cited figure.</p>" +
        "<p><b>Whose fault it is.</b> Attribution belongs to the investigation. A traffic light that guessed would be the worst thing this dashboard could do.</p></div>";
      return;
    }
    const financial = data.financial_impact || {};
    const merchants = (state.merchants || []).map(function (row) {
      const sev = document.createElement("div");
      sev.appendChild(badgePair(row.highest_severity, "__omit__", null).querySelector(".sev"));
      return (
        '<article class="merchant-card">' +
        "<h3>" + escapeHtml(row.scope_label || row.merchant_id || "Platform-wide") + "</h3>" +
        "<p>Highest stored severity: " + sev.innerHTML + "</p>" +
        "<p>Active incidents: " + escapeHtml(String(row.active_incident_count)) + "</p>" +
        "</article>"
      );
    }).join("");
    overviewBoard.innerHTML =
      '<div class="kpis">' +
      kpi(kpiKind(headline.severity), "Service status", String(headline.severity || "not in store"), "severity on incident " + sourceId, "overview-severity") +
      kpi("grey", "Current conversion", ratio(data.current_conversion), "from incident " + sourceId, "overview-actual") +
      kpi("grey", "Expected conversion", ratio(data.expected_conversion), "copied from the same record", "overview-expected") +
      kpi("grey", "GMV", money(data.gmv), "attempted_value on that incident", "overview-gmv") +
      kpi("grey", "GMV at risk", money(data.estimated_gmv_at_risk), "gmv_at_risk on that incident", "overview-risk") +
      kpi("grey", "Costing / hour", money(financial.loss_per_hour), "loss_per_hour on that incident", "overview-burn") +
      kpi("grey", "Active incidents", String(data.active_incident_count), "count of stored records") +
      "</div>" +
      '<div class="note warn tight"><h4>Two things this header deliberately does not show</h4>' +
      "<p><b>A portfolio total.</b> Adding cited <code>loss_per_hour</code> figures would be a number that exists only here. A real total has to come from W2 as its own cited figure.</p>" +
      "<p><b>Whose fault it is.</b> Attribution belongs to the investigation. A traffic light that guessed would be the worst thing this dashboard could do.</p></div>" +
      "<h3>Merchant health</h3>" +
      '<div class="merchants">' + (merchants || '<p class="empty">No merchant incidents in the store.</p>') + "</div>";
    bindCites(overviewBoard);
  }

  function renderQueue() {
    if (!state.queue.length) {
      queueBoard.innerHTML = '<p class="empty">No incidents in the store.</p>';
      queueWho.textContent = "Ordered by stored severity. Recency is not a ranking.";
      return;
    }
    queueWho.textContent = state.queue.length + " in the store · ordered by stored severity. Recency is not a ranking.";
    const table = document.createElement("div");
    table.className = "frame";
    table.innerHTML =
      '<div class="frame-bar"><span class="dot"></span><span class="path mono">/incidents</span></div>' +
      '<div class="frame-body"><div class="tbl-scroll"><table><thead><tr>' +
      "<th>Priority</th><th>Slice</th><th class=\"num\">Approval</th><th class=\"num\">$ / hour</th><th>Diagnosis</th><th></th>" +
      "</tr></thead><tbody></tbody></table></div></div>";
    const tbody = table.querySelector("tbody");
    state.queue.forEach(function (item) {
      const row = document.createElement("tr");
      row.className = "rank-row";
      const change = item.change || {};
      const financial = item.financial_impact || {};
      const priority = document.createElement("td");
      priority.appendChild(badgePair(item.severity, null, item.lifecycle_state).querySelector(".sev"));
      const lifecycle = document.createElement("span");
      lifecycle.className = "sub mono";
      lifecycle.textContent = item.lifecycle_state || "not in store";
      priority.appendChild(lifecycle);
      const slice = document.createElement("td");
      slice.className = "cohort";
      slice.innerHTML = "<b>" + escapeHtml(incidentScope(item)) + "</b>" +
        '<span class="sub mono">' + escapeHtml(cohortLine(item.affected_cohort)) + "</span>";
      const approval = document.createElement("td");
      approval.className = "num";
      approval.innerHTML = "<b>" + escapeHtml(ratio(change.actual)) + "</b>" +
        '<span class="sub">expected ' + escapeHtml(ratio(change.expected)) + "</span>";
      const burn = document.createElement("td");
      burn.className = "num";
      burn.innerHTML = '<span class="money">' + escapeHtml(money(financial.loss_per_hour)) + "</span>";
      const diagnosis = document.createElement("td");
      const confWrap = badgePair(item.severity, item.diagnostic_confidence, item.lifecycle_state);
      diagnosis.appendChild(confWrap.querySelector(".conf"));
      const openCell = document.createElement("td");
      const open = document.createElement("span");
      open.className = "q-open";
      open.textContent = "Open";
      openCell.appendChild(open);
      row.appendChild(priority);
      row.appendChild(slice);
      row.appendChild(approval);
      row.appendChild(burn);
      row.appendChild(diagnosis);
      row.appendChild(openCell);
      row.addEventListener("click", function () {
        state.selectedId = item.incident_id;
        loadDetail(item.incident_id).then(function () {
          setView("detail");
        });
      });
      tbody.appendChild(row);
    });
    queueBoard.innerHTML = "";
    queueBoard.appendChild(table);
  }

  function readoutCell(label, value, hint, citeId) {
    const cited = citeId
      ? '<span class="fig">' + escapeHtml(value) + citeButton(citeId, "cite " + label) + "</span>"
      : escapeHtml(value);
    return "<div><dt>" + escapeHtml(label) + "</dt><dd>" + cited +
      (hint ? "<small>" + escapeHtml(hint) + "</small>" : "") + "</dd></div>";
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
    const financial = incident.financial_impact || {};
    const change = incident.change || {};
    const persistence = incident.persistence || {};
    const banner = statusBanner(incident, investigation);
    const channels = detail.escalation || [];
    const outcome = investigation.outcome;
    const frame = document.createElement("div");
    frame.className = "frame";
    const path = "/incidents/" + (incident.incident_id || "");
    const head = document.createElement("div");
    head.className = "headline";
    const lead = document.createElement("div");
    lead.className = "lead";
    const title = document.createElement("h3");
    title.textContent = incidentScope(incident);
    lead.appendChild(title);
    lead.appendChild(badgePair(
      incident.severity,
      (investigation.result || {}).diagnostic_confidence,
      incident.lifecycle_state
    ));
    const meta = document.createElement("p");
    meta.className = "meta cohort";
    meta.textContent = (incident.incident_id || "") + " · " + (incident.lifecycle_state || "") +
      (outcome ? " · " + outcome : "") +
      (channels.length ? " · " + channels.map(function (event) {
        return (event.channel || "channel") + ": " + (event.status || "not in store");
      }).join(" · ") : "");
    lead.appendChild(meta);
    head.appendChild(lead);
    const lost = financial.estimated_lost_approved_volume || {};
    const chanCards = channels.length
      ? '<div class="panel"><h3>Escalation</h3><p class="hint">Severity routes. Confidence never does. These are stored outcomes, not controls.</p><div class="chan">' +
        channels.map(function (event) {
          const armed = event.status === "delivered" || event.status === "fallback_dashboard";
          return '<article class="chan-card' + (armed ? " armed" : "") + '"><h4>' +
            escapeHtml(event.channel || "channel") + "</h4><p class=\"state\">" +
            escapeHtml(event.status || "not in store") + "</p></article>";
        }).join("") +
        "</div></div>"
      : '<div class="panel"><h3>Escalation</h3><p class="empty">No escalation outcome is stored for this incident.</p></div>';
    const diagnosisPair = badgePair(
      incident.severity,
      (investigation.result || {}).diagnostic_confidence,
      incident.lifecycle_state
    );
    const diagnosis = '<div class="panel"><h3>Diagnosis</h3><div class="dual">' +
      '<div><span class="dual-l">priority</span>' + diagnosisPair.querySelector(".sev").outerHTML + "</div>" +
      '<div><span class="dual-l">confidence</span>' + diagnosisPair.querySelector(".conf").outerHTML + "</div>" +
      "</div></div>";
    frame.innerHTML =
      '<div class="frame-bar"><span class="dot"></span><span class="path mono">' + escapeHtml(path) + "</span></div>" +
      '<div class="frame-body"></div>';
    const body = frame.querySelector(".frame-body");
    body.insertAdjacentHTML("beforeend", banner);
    body.appendChild(head);
    body.insertAdjacentHTML("beforeend",
      '<dl class="readout">' +
      readoutCell("Approval now", ratio(change.actual), "expected " + ratio(change.expected), "detail-actual") +
      readoutCell("At risk so far", money(financial.gmv_at_risk), "this window", "detail-risk") +
      readoutCell("Costing / hour", money(financial.loss_per_hour), "if sustained", "detail-burn") +
      readoutCell("Payments lost", count(lost.payments), "from the incident record", "detail-lost") +
      readoutCell("Onset", fmt(incident.onset), "from the incident record", "detail-onset") +
      readoutCell("Observed for", persistence.observed_for_seconds == null ? "not in store" : String(persistence.observed_for_seconds) + "s", "stored persistence, not a clock", "detail-persist") +
      "</dl>"
    );
    const grid = document.createElement("div");
    grid.className = "detail-grid";
    const left = document.createElement("div");
    left.appendChild(questionCard("1. What changed?", questions.what_changed, "Copied from the incident change block."));
    left.appendChild(questionCard("2. Where?", questions.where, "Affected cohort as stored."));
    left.appendChild(questionCard("3. How much does it matter?", questions.how_much_it_matters, "Financial impact as stored."));
    const right = document.createElement("div");
    const narrativeBody = questions.narrative_available
      ? null
      : narrativePlaceholder(incident, investigation);
    right.appendChild(questionCard(
      "4. What probably caused it?",
      questions.narrative_available ? questions.what_probably_caused_it : narrativeBody,
      "Investigation narrative. Never guessed in the UI."
    ));
    right.appendChild(questionCard(
      "5. Why do we believe that?",
      questions.narrative_available ? questions.why_we_believe_that : narrativeBody,
      "Confirmed facts, competing explanations, missing evidence."
    ));
    right.appendChild(questionCard(
      "6. What should the TAM do?",
      questions.narrative_available ? questions.what_the_operator_should_do : narrativeBody,
      "Recommended next action from the investigation record."
    ));
    grid.appendChild(left);
    grid.appendChild(right);
    body.appendChild(grid);
    body.insertAdjacentHTML("beforeend", chanCards);
    body.insertAdjacentHTML("beforeend", diagnosis);
    detailBoard.innerHTML = "";
    detailBoard.appendChild(frame);
    bindCites(detailBoard);
  }

  function questionCard(title, body, hint) {
    const article = document.createElement("article");
    article.className = "panel question";
    const heading = document.createElement("h3");
    heading.textContent = title;
    article.appendChild(heading);
    if (hint) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = hint;
      article.appendChild(p);
    }
    const pre = document.createElement("pre");
    pre.textContent = pretty(body);
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
      ? '<div class="note warn tight banner"><h4>Investigation is running</h4><p>This usually takes about a minute.</p></div>'
      : (investigation.narrative_available
        ? ""
        : '<div class="note warn tight banner"><h4>Narrative unavailable</h4><p>The trail still shows every query that ran.</p></div>');
    if (!trail.length) {
      const empty = running
        ? '<p class="empty">Investigation is running. The evidence trail is stored when it finishes.</p>'
        : '<p class="empty">No evidence trail is stored for this incident.</p>';
      evidenceBoard.innerHTML = banner + empty;
      return;
    }
    const frame = document.createElement("div");
    frame.className = "frame";
    frame.innerHTML =
      '<div class="frame-bar"><span class="dot"></span><span class="path mono">/incidents/' +
      escapeHtml(incident.incident_id || "") +
      "/evidence</span></div><div class=\"frame-body\"></div>";
    const body = frame.querySelector(".frame-body");
    body.insertAdjacentHTML("beforeend", banner);
    const trailWrap = document.createElement("div");
    trailWrap.className = "trail";
    trail.forEach(function (entry) {
      const card = document.createElement("article");
      card.className = "trail-card";
      const citeId = "trail-" + String(entry.sequence);
      card.innerHTML =
        "<h3>Query " + escapeHtml(String(entry.sequence)) + " · " + escapeHtml(entry.tool || "") +
        " " + citeButton(citeId, "cite query " + String(entry.sequence)) + "</h3>" +
        '<p class="cohort">' + escapeHtml(entry.query_id || "") + " · " + escapeHtml(entry.timestamp || "") +
        " · " + escapeHtml(entry.outcome || "") + "</p>" +
        '<div class="asked"><strong>Asked</strong><pre>' + escapeHtml(pretty(entry.parameters)) + "</pre></div>" +
        '<div class="returned"><strong>Returned</strong><pre>' + escapeHtml(pretty(entry.response)) + "</pre></div>";
      trailWrap.appendChild(card);
    });
    body.appendChild(trailWrap);
    evidenceBoard.innerHTML = "";
    evidenceBoard.appendChild(frame);
    bindCites(evidenceBoard);
  }


  // ------------------------------------------------------------------ escalation

  function escalationGroup() {
    const data = state.escalations;
    if (!data || !(data.incidents || []).length) return null;
    const wanted = state.selectedId;
    const match = (data.incidents || []).filter(function (group) {
      return group.incident_id === wanted;
    })[0];
    return match || data.incidents[0];
  }

  const CHANNEL_ORDER = ["dashboard", "slack", "phone"];
  const CHANNEL_NOTE = {
    dashboard: "Always on. This page is the channel.",
    slack: "Block Kit message on the configured channel.",
    phone: "A silent call. The call ringing is the signal, not spoken content.",
  };

  function bindingColumns(binding) {
    const seen = [];
    CHANNEL_ORDER.forEach(function (channel) {
      const used = (binding || []).some(function (row) {
        return (row.channels || []).indexOf(channel) !== -1;
      });
      if (used) seen.push(channel);
    });
    (binding || []).forEach(function (row) {
      (row.channels || []).forEach(function (channel) {
        if (seen.indexOf(channel) === -1) seen.push(channel);
      });
    });
    return seen;
  }

  // Drawn, not decided: every row and dot below is one entry of the binding the
  // server read out of the escalator itself.
  function bindingFigure(binding, severity) {
    const rows = binding || [];
    if (!rows.length) return "";
    const columns = bindingColumns(binding);
    const rowHeight = 26;
    const top = 34;
    const height = top + rows.length * rowHeight + 4;
    const colX = columns.map(function (_, index) { return 168 + index * 148; });
    const here = String(severity || "").toLowerCase();
    let svg = '<svg viewBox="0 0 640 ' + height + '" width="100%" role="img" aria-label="Severity to channel binding, read from the escalator">' +
      "<title>Severity to channel binding</title><g class=\"bindg\">" +
      '<text x="0" y="14" class="bind-h">SEVERITY</text>';
    columns.forEach(function (channel, index) {
      svg += '<text x="' + colX[index] + '" y="14" class="bind-h">' + escapeHtml(channel.toUpperCase()) + "</text>";
    });
    rows.forEach(function (row, index) {
      const y = top + index * rowHeight;
      const label = String(row.severity || "");
      const active = label.toLowerCase() === here;
      if (active) {
        svg += '<rect x="-8" y="' + (y - 14) + '" width="600" height="24" rx="6" class="bind-here ' +
          severityClass(label) + '"/>';
      }
      svg += '<text x="0" y="' + y + '" class="bind-l ' + severityClass(label) + '">' + escapeHtml(label) + "</text>";
      columns.forEach(function (channel, column) {
        const on = (row.channels || []).indexOf(channel) !== -1;
        svg += '<circle cx="' + (colX[column] + 12) + '" cy="' + (y - 4) + '" r="6" class="bind-dot ' +
          (on ? "on " + severityClass(label) : "off") + '"/>';
      });
      if (active) {
        svg += '<text x="' + (colX[columns.length - 1] + 44) + '" y="' + y + '" class="bind-here-l ' +
          severityClass(label) + '">this incident</text>';
      }
    });
    return svg + "</g></svg>";
  }

  function channelCards(group) {
    const fired = {};
    (group.channels || []).forEach(function (event) { fired[event.channel] = event; });
    const names = (group.expected_channels || []).slice();
    (group.channels || []).forEach(function (event) {
      if (names.indexOf(event.channel) === -1) names.push(event.channel);
    });
    if (!names.length) return '<p class="empty">No escalation outcome is stored for this incident.</p>';
    return '<div class="chan">' + names.map(function (name) {
      const event = fired[name];
      const status = event ? event.status : "not in store";
      const armed = status === "delivered" || status === "fallback_dashboard";
      return '<article class="chan-card' + (armed ? " armed" : "") + '"><h4>' +
        escapeHtml(name) + "</h4><p>" + escapeHtml(CHANNEL_NOTE[name] || "Stored channel outcome.") + "</p>" +
        '<p class="state">' + escapeHtml(status) +
        (event && event.detail ? " · " + escapeHtml(event.detail) : "") + "</p>" +
        '<p class="state mono">' + escapeHtml((event && event.created_at) || "not in store") + "</p>" +
        "</article>";
    }).join("") + "</div>";
  }

  function slackChannelName() {
    const data = state.escalations || {};
    return data.slack_channel || "slack channel not in store";
  }

  function slackPanel(group) {
    const payload = group.payload || {};
    const change = payload.change || {};
    const financial = payload.financial_impact || {};
    const lost = financial.estimated_lost_approved_volume || {};
    const citations = payload.citations || {};
    const keys = Object.keys(citations);
    const event = (group.channels || []).filter(function (item) { return item.channel === "slack"; })[0];
    const hypothesis = payload.leading_hypothesis;
    const sevClass = severityClass(payload.severity || group.severity);
    return '<div class="panel"><h3>Slack</h3>' +
      '<p class="hint">The payload that was rendered into Block Kit, copied field for field. Status: ' +
      escapeHtml((event && event.status) || "not in store") + ".</p>" +
      '<div class="slackish"><div class="bar mono"><span>' +
      escapeHtml(slackChannelName()) + "</span><span>" +
      escapeHtml((event && event.created_at) || "not in store") + "</span></div>" +
      '<div class="msg"><div class="av" aria-hidden="true"><span class="avmark"></span></div>' +
      '<div class="msg-body"><div class="who">Control Tower <em>APP</em></div>' +
      '<div class="block ' + sevClass + '">' +
      "<p><b>" + escapeHtml(String(payload.severity || group.severity || "not in store")) + " · " +
      escapeHtml(group.scope_label || "Platform-wide") + "</b></p>" +
      "<p>" +
      '<span class="fig">' + escapeHtml(ratio(change.expected)) + citeButton("esc-expected", "cite expected conversion") + "</span>" +
      " &rarr; " +
      '<span class="fig">' + escapeHtml(ratio(change.actual)) + citeButton("esc-actual", "cite current conversion") + "</span>" +
      " since " + escapeHtml(String(payload.onset || group.onset || "not in store")) + ". " +
      '<span class="fig">' + escapeHtml(count(lost.payments)) + citeButton("esc-lost", "cite payments lost") + "</span>" +
      " payments lost, " +
      '<span class="fig">' + escapeHtml(money(financial.gmv_at_risk)) + citeButton("esc-risk", "cite GMV at risk") + "</span>" +
      " at risk, " +
      '<span class="fig">' + escapeHtml(money(financial.loss_per_hour)) + citeButton("esc-burn", "cite loss per hour") + "</span>" +
      "/hr if sustained.</p>" +
      "<p>Diagnostic confidence: <b>" +
      escapeHtml(confidenceLabel(payload.diagnostic_confidence, group.lifecycle_state)) + "</b>. " +
      escapeHtml(hypothesis ? String(hypothesis) : "No causal narrative is stored for this incident.") +
      "</p></div>" +
      (keys.length
        ? '<div class="ctx mono">' + keys.map(function (key) {
            return "<span>" + escapeHtml(String(citations[key])) + "</span>";
          }).join("") + "</div>"
        : '<div class="ctx mono"><span>no citations stored</span></div>') +
      "</div></div></div></div>";
  }

  function phonePanel(group) {
    const event = (group.channels || []).filter(function (item) { return item.channel === "phone"; })[0];
    const calls = (state.calls || []);
    const mine = calls.filter(function (call) { return call.incident_id === group.incident_id; });
    const rows = calls.length
      ? '<div class="calls">' + calls.map(function (call) {
          const payload = call.payload || {};
          return '<article class="call' + (call.incident_id === group.incident_id ? " here" : "") + '">' +
            "<b>" + escapeHtml(String(payload.severity || "not in store")) + " · " +
            escapeHtml(String(payload.scope_label || "Platform-wide")) + "</b>" +
            '<span class="sub mono">' + escapeHtml(String(call.incident_id)) + "</span>" +
            '<span class="sub mono">queued ' + escapeHtml(String(call.created_at || "not in store")) + "</span>" +
            "</article>";
        }).join("") + "</div>"
      : '<p class="empty">No call is queued. A call is only queued when the phone channel could not place it.</p>';
    return '<div class="panel"><h3>Phone</h3>' +
      '<p class="hint">Status: ' + escapeHtml((event && event.status) || "not in store") +
      (event && event.detail ? " · " + escapeHtml(event.detail) : "") + ".</p>" +
      '<div class="script"><p>The call carries no spoken script. It is a bounded silent call: the call ringing is the deterministic signal, and the dashboard carries the detail.</p></div>' +
      '<p class="foot">Pending calls come from <code>/api/calls</code>' +
      (mine.length ? ", and this incident is one of them." : ".") + "</p>" +
      rows +
      "</div>";
  }

  function blastPanel(group) {
    const blast = group.blast_radius || {};
    const keys = Object.keys(blast);
    if (!keys.length) {
      return '<div class="panel"><h3>Blast radius</h3><p class="empty">No blast radius is stored for this incident.</p></div>';
    }
    return '<div class="panel"><h3>Blast radius</h3><div class="br">' +
      keys.sort().map(function (key) {
        return '<span class="br-i"><b>' + escapeHtml(count(blast[key])) +
          citeButton("esc-blast:" + key, "cite " + key) + "</b>" +
          escapeHtml(key.replace(/^affected_/, "").replace(/_/g, " ")) +
          "<em>" + escapeHtml(key) + "</em></span>";
      }).join("") +
      '</div><p class="foot">Every count is a field on the stored incident record. The dashboard adds nothing to them.</p></div>';
  }

  function renderEscalation() {
    const data = state.escalations;
    if (!data) {
      escalationBoard.innerHTML = '<p class="empty">Waiting for the store.</p>';
      return;
    }
    const group = escalationGroup();
    if (!group) {
      escalationBoard.innerHTML =
        '<p class="empty">No incident has escalated yet. Channels fire when an incident is stored.</p>' +
        '<div class="frame"><div class="frame-bar"><span class="dot"></span><span class="path mono">/escalations</span></div>' +
        '<div class="frame-body"><figure class="chart span-full binding">' + bindingFigure(data.binding, null) +
        '<figcaption>Severity routes; diagnostic confidence never does. Read from the escalator, not restated here.</figcaption></figure></div></div>';
      return;
    }
    const frame = document.createElement("div");
    frame.className = "frame";
    frame.innerHTML =
      '<div class="frame-bar"><span class="dot"></span><span class="path mono">/incidents/' +
      escapeHtml(String(group.incident_id || "")) + '/escalation</span></div>' +
      '<div class="frame-body">' +
      '<figure class="chart span-full binding">' + bindingFigure(data.binding, group.severity) +
      '<figcaption>Severity routes; diagnostic confidence never does. Read from the escalator, not restated here.</figcaption></figure>' +
      '<h3 class="sec">Channels fired for <span class="sec-id mono">' +
      escapeHtml(String(group.incident_id || "")) + "</span></h3>" +
      channelCards(group) +
      '<div class="detail-grid esc-grid"><div>' + slackPanel(group) + blastPanel(group) +
      "</div><div>" + phonePanel(group) + "</div></div>" +
      "</div>";
    escalationBoard.innerHTML = "";
    escalationBoard.appendChild(frame);
    bindCites(escalationBoard);
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

  function citeRecord(citeId) {
    const overview = state.overview || {};
    const headline = (overview.incidents || [])[0] || {};
    const detail = state.detail || {};
    const incident = detail.incident || {};
    const investigation = detail.investigation || {};
    const financial = (incident.financial_impact || overview.financial_impact || {});
    const change = (incident.change || overview.change || {});
    const trail = detail.evidence_trail || [];
    if (citeId && citeId.indexOf("trail-") === 0) {
      const sequence = Number(citeId.slice("trail-".length));
      const entry = trail.filter(function (item) { return Number(item.sequence) === sequence; })[0];
      if (!entry) return null;
      return {
        title: (entry.tool || "query") + " · " + (entry.query_id || "not in store"),
        lede: "Copied from the stored evidence trail. The UI did not recompute this.",
        rows: [
          ["tool", entry.tool || "not in store"],
          ["query_id", entry.query_id || "not in store"],
          ["timestamp", entry.timestamp || "not in store"],
          ["outcome", entry.outcome || "not in store"],
        ],
        body: { parameters: entry.parameters, response: entry.response },
      };
    }
    if (citeId && citeId.indexOf("esc-") === 0) return escalationCite(citeId);
    const table = {
      "overview-severity": { title: "Incident severity", field: "incident.severity", value: headline.severity, source: overview.source_incident_id },
      "overview-actual": { title: "Current conversion", field: "change.actual", value: overview.current_conversion, source: overview.source_incident_id },
      "overview-expected": { title: "Expected conversion", field: "change.expected", value: overview.expected_conversion, source: overview.source_incident_id },
      "overview-gmv": { title: "GMV", field: "financial_impact.attempted_value", value: overview.gmv, source: overview.source_incident_id },
      "overview-risk": { title: "GMV at risk", field: "financial_impact.gmv_at_risk", value: overview.estimated_gmv_at_risk, source: overview.source_incident_id },
      "overview-burn": { title: "Costing / hour", field: "financial_impact.loss_per_hour", value: (overview.financial_impact || {}).loss_per_hour, source: overview.source_incident_id },
      "detail-actual": { title: "Approval now", field: "change.actual", value: change.actual, source: incident.incident_id },
      "detail-risk": { title: "At risk so far", field: "financial_impact.gmv_at_risk", value: financial.gmv_at_risk, source: incident.incident_id },
      "detail-burn": { title: "Costing / hour", field: "financial_impact.loss_per_hour", value: financial.loss_per_hour, source: incident.incident_id },
      "detail-lost": { title: "Payments lost", field: "financial_impact.estimated_lost_approved_volume.payments", value: (financial.estimated_lost_approved_volume || {}).payments, source: incident.incident_id },
      "detail-onset": { title: "Onset", field: "incident.onset", value: incident.onset, source: incident.incident_id },
      "detail-persist": { title: "Observed for", field: "persistence.observed_for_seconds", value: (incident.persistence || {}).observed_for_seconds, source: incident.incident_id },
    };
    const rec = table[citeId];
    if (!rec) return null;
    return {
      title: rec.title,
      lede: "Copied from the incident record in the store. No figure here is derived in the UI.",
      rows: [
        ["source", rec.source || "not in store"],
        ["field", rec.field],
        ["value", rec.value == null || rec.value === "" ? "not in store" : rec.value],
      ],
      body: rec.value,
    };
  }


  // Escalation citations name the escalation_event payload the figure was
  // copied out of, the same way an incident citation names the incident record.
  function escalationCite(citeId) {
    const group = escalationGroup();
    if (!group) return null;
    const payload = group.payload || {};
    const change = payload.change || {};
    const financial = payload.financial_impact || {};
    const lost = financial.estimated_lost_approved_volume || {};
    const blast = group.blast_radius || {};
    if (citeId.indexOf("esc-blast:") === 0) {
      const key = citeId.slice("esc-blast:".length);
      return {
        title: "Blast radius · " + key,
        lede: "Copied from blast_radius on the stored incident record. The dashboard counts nothing.",
        rows: [
          ["source", group.incident_id || "not in store"],
          ["field", "blast_radius." + key],
          ["value", blast[key] == null ? "not in store" : blast[key]],
        ],
        body: blast,
      };
    }
    const table = {
      "esc-expected": { title: "Expected conversion", field: "change.expected", value: change.expected },
      "esc-actual": { title: "Current conversion", field: "change.actual", value: change.actual },
      "esc-lost": {
        title: "Payments lost",
        field: "financial_impact.estimated_lost_approved_volume.payments",
        value: lost.payments,
      },
      "esc-risk": { title: "GMV at risk", field: "financial_impact.gmv_at_risk", value: financial.gmv_at_risk },
      "esc-burn": { title: "Costing / hour", field: "financial_impact.loss_per_hour", value: financial.loss_per_hour },
    };
    const rec = table[citeId];
    if (!rec) return null;
    return {
      title: rec.title,
      lede: "Copied from the escalation_event payload that was sent to the channel. No figure here is derived in the UI.",
      rows: [
        ["source", group.incident_id || "not in store"],
        ["record", "escalation_event.payload"],
        ["field", rec.field],
        ["value", rec.value == null || rec.value === "" ? "not in store" : rec.value],
      ],
      body: rec.value,
    };
  }

  function openCite(citeId) {
    const rec = citeRecord(citeId);
    if (!rec) return;
    drawerTitle.textContent = rec.title;
    drawerLede.textContent = rec.lede;
    drawerBody.innerHTML =
      "<dl>" + rec.rows.map(function (row) {
        return "<dt>" + escapeHtml(row[0]) + "</dt><dd class=\"mono\">" + escapeHtml(pretty(row[1])) + "</dd>";
      }).join("") + "</dl><h4>Record</h4><pre>" + escapeHtml(pretty(rec.body)) + "</pre>";
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    scrim.classList.add("on");
  }

  function closeCite() {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    scrim.classList.remove("on");
  }

  function bindCites(root) {
    root.querySelectorAll("button.cite").forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        openCite(btn.getAttribute("data-cite"));
      });
    });
  }

  function jsonGet(path) {
    return fetch(path, { cache: "no-store" }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw body;
        return body;
      });
    });
  }

  // Every view reads its own endpoint. The queue is the whole stored queue from
  // /api/incidents, not the active slice the overview header is built from, and
  // merchant health, pending calls and escalation outcomes each come from the
  // endpoint that owns them rather than being re-derived off one payload.
  function refresh() {
    return Promise.all([
      jsonGet("/api/overview"),
      jsonGet("/api/incidents"),
      jsonGet("/api/merchants"),
      jsonGet("/api/calls"),
      jsonGet("/api/escalations"),
    ]).then(function (payloads) {
      state.overview = payloads[0];
      state.queue = payloads[1].incidents || [];
      state.merchants = payloads[2].merchants || [];
      state.calls = payloads[3].calls || [];
      state.escalations = payloads[4];
      if (!state.selectedId && state.queue.length) state.selectedId = state.queue[0].incident_id;
      renderOverview();
      renderQueue();
      renderEscalation();
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
      renderEscalation();
    });
  }

  document.querySelectorAll(".jump-link").forEach(function (btn) {
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

  $("drawer-close").addEventListener("click", closeCite);
  scrim.addEventListener("click", closeCite);

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
