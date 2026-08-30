(function () {
  "use strict";

  const state = {
    view: "overview",
    selectedId: null,
    overview: null,
    queue: [],
    detail: null,
    injected: false,
  };

  const overviewBoard = document.getElementById("overview-board");
  const queueBoard = document.getElementById("queue-board");
  const detailBoard = document.getElementById("detail-board");
  const evidenceBoard = document.getElementById("evidence-board");
  const escalationBoard = document.getElementById("escalation-board");
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
    const amount = Number(value.amount);
    if (!Number.isFinite(amount)) return "not in store";
    const formatted = amount.toLocaleString("en-US", {
      minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
      maximumFractionDigits: 2,
    });
    return (currency ? currency + " " : "") + formatted;
  }

  function numberValue(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function amountValue(value) {
    if (!value || typeof value !== "object") return null;
    const amount = numberValue(value.amount);
    return amount === null ? null : { amount: amount, currency: value.currency || "" };
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

  function kpi(kind, label, value, hint, citeId) {
    const cited = citeId
      ? '<span class="fig">' + escapeHtml(value) + citeButton(citeId, "cite " + label) + "</span>"
      : escapeHtml(value);
    return (
      '<div class="kpi kpi-' + kind + '">' +
      lampHtml(kind) +
      "<dt>" + escapeHtml(label) + "</dt><dd>" + cited + "</dd>" +
      "<p>" + escapeHtml(hint) + "</p></div>"
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
    const merchants = (data.merchant_health || []).map(function (row) {
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

  function amountChart(title, description, items, valueKey, tone, citeItem, secondaryKey) {
    const values = items.map(function (item) {
      const financial = item.financial_impact || {};
      const amount = amountValue(financial[valueKey]);
      const secondary = secondaryKey ? amountValue(financial[secondaryKey]) : null;
      return secondaryKey ? [amount, secondary] : [amount];
    });
    const finite = values.reduce(function (all, pair) {
      return all.concat(pair.filter(function (value) { return value !== null; }).map(function (value) { return value.amount; }));
    }, []);
    const max = finite.length ? Math.max.apply(null, finite) : 0;
    const rows = items.map(function (item, index) {
      const pair = values[index];
      const label = incidentScope(item);
      const tracks = pair.map(function (amount, pairIndex) {
        const width = amount && max > 0 ? Math.max(2, amount.amount / max * 100) : 0;
        const key = pairIndex === 0 ? valueKey : secondaryKey;
        return '<div class="bar-line"><span class="bar-key">' + escapeHtml(key.replace(/_/g, " ")) + '</span><span class="bar-track"><span class="bar ' +
          escapeHtml(tone(item)) + (pairIndex ? " secondary" : "") + '" style="--bar-width:' + width.toFixed(2) + '%"></span></span><span class="bar-value mono">' +
          escapeHtml(amount ? money(amount) : "not in store") + '</span></div>';
      }).join("");
      return '<div class="bar-row"><div class="bar-label">' + escapeHtml(label) + '</div>' + tracks + '</div>';
    }).join("");
    const cite = citeItem ? citeButton("queue-" + valueKey + "-" + String(citeItem.incident_id), "cite " + valueKey) : "";
    const secondaryCite = citeItem && secondaryKey ? citeButton("queue-" + secondaryKey + "-" + String(citeItem.incident_id), "cite " + secondaryKey) : "";
    return '<figure class="chart"><figcaption class="fc-top">' + escapeHtml(description) + '</figcaption>' +
      '<div class="bar-chart">' + (rows || '<p class="empty">No stored values.</p>') + '</div>' +
      '<figcaption><span class="fig">' + escapeHtml(title) + cite + secondaryCite + '</span> · bars are relative to this returned set; currency stays in each value.</figcaption></figure>';
  }

  function sparkline(item) {
    const change = item.change || {};
    const expected = numberValue(change.expected);
    const actual = numberValue(change.actual);
    if (expected === null || actual === null) return '<span class="sub">not in store</span>';
    function point(value) {
      const bounded = Math.max(0, Math.min(1, value));
      return (26 - bounded * 20).toFixed(1);
    }
    return '<svg viewBox="0 0 120 30" class="spark" role="img" aria-label="Stored approval comparison: expected ' +
      escapeHtml(ratio(expected)) + ', actual ' + escapeHtml(ratio(actual)) + '"><line x1="0" y1="6" x2="120" y2="6" class="spark-baseline" />' +
      '<polyline points="0,' + point(expected) + ' 120,' + point(actual) + '" class="spark-line ' + escapeHtml(severityClass(item.severity)) + '" /></svg>';
  }

  function trajectory(item) {
    const change = item.change || {};
    const expected = numberValue(change.expected);
    const actual = numberValue(change.actual);
    if (expected === null || actual === null) return '<span class="traj flat">not in store</span>';
    if (actual < expected) {
      return '<span class="traj worse"><svg width="13" height="9" viewBox="0 0 14 10" fill="none" aria-hidden="true"><path d="M1 1.5 7 8.5l6-7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>Below expected</span>';
    }
    if (actual > expected) {
      return '<span class="traj better"><svg width="13" height="9" viewBox="0 0 14 10" fill="none" aria-hidden="true"><path d="m1 8.5 6-7 6 7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>Above expected</span>';
    }
    return '<span class="traj flat"><svg width="13" height="9" viewBox="0 0 14 10" fill="none" aria-hidden="true"><path d="M1 5h12" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>At expected</span>';
  }

  function renderQueue() {
    if (!state.queue.length) {
      queueBoard.innerHTML = '<p class="empty">No incidents in the store.</p>';
      queueWho.textContent = "Ordered by stored severity. Recency is not a ranking.";
      return;
    }
    queueWho.textContent = state.queue.length + " open · ordered by stored severity. Recency is not a ranking.";
    const table = document.createElement("div");
    table.className = "frame";
    table.innerHTML =
      '<div class="frame-bar"><span class="dot"></span><span class="path mono">/incidents</span></div>' +
      '<div class="frame-body"><div class="split-2 queue-charts"></div>' +
      '<div class="tbl-scroll"><table><thead><tr>' +
      "<th>Priority</th><th>Slice</th><th>Stored change</th><th class=\"num\">Approval</th><th class=\"num\">$ / hour</th><th>Trajectory</th><th>Diagnosis</th><th></th>" +
      "</tr></thead><tbody></tbody></table></div></div>";
    const charts = table.querySelector(".queue-charts");
    charts.innerHTML = amountChart(
      "Attempted value and GMV at risk",
      "Value moving through each affected slice, and how much of it is not landing.",
      state.queue,
      "attempted_value",
      function (item) { return severityClass(item.severity); },
      state.queue[0],
      "gmv_at_risk"
    ) + amountChart(
      "Loss per hour",
      "What each stored incident is costing per hour if it continues.",
      state.queue,
      "loss_per_hour",
      function (item) { return severityClass(item.severity); },
      state.queue[0]
    );
    const tbody = table.querySelector("tbody");
    state.queue.forEach(function (item) {
      const row = document.createElement("tr");
      row.className = "rank-row";
      const change = item.change || {};
      const financial = item.financial_impact || {};
      const priority = document.createElement("td");
      priority.appendChild(badgePair(item.severity, null, item.lifecycle_state).querySelector(".sev"));
      const slice = document.createElement("td");
      slice.className = "cohort";
      slice.innerHTML = "<b>" + escapeHtml(incidentScope(item)) + "</b>" +
        '<span class="sub mono">' + escapeHtml(cohortLine(item.affected_cohort)) + "</span>";
      const storedChange = document.createElement("td");
      storedChange.className = "stored-change";
      storedChange.innerHTML = sparkline(item);
      const approval = document.createElement("td");
      approval.className = "num";
      approval.innerHTML = "<b>" + escapeHtml(ratio(change.actual)) + "</b>" +
        '<span class="sub">expected ' + escapeHtml(ratio(change.expected)) + "</span>";
      const burn = document.createElement("td");
      burn.className = "num";
      burn.innerHTML = '<span class="fig money">' + escapeHtml(money(financial.loss_per_hour)) +
        citeButton("queue-loss_per_hour-" + String(item.incident_id), "cite loss per hour") + "</span>";
      const trend = document.createElement("td");
      trend.innerHTML = trajectory(item);
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
      row.appendChild(storedChange);
      row.appendChild(approval);
      row.appendChild(burn);
      row.appendChild(trend);
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
    bindCites(queueBoard);
  }

  function readoutCell(label, value, hint, citeId) {
    const cited = citeId
      ? '<span class="fig">' + escapeHtml(value) + citeButton(citeId, "cite " + label) + "</span>"
      : escapeHtml(value);
    return "<div><dt>" + escapeHtml(label) + "</dt><dd>" + cited +
      (hint ? "<small>" + escapeHtml(hint) + "</small>" : "") + "</dd></div>";
  }

  function metricBar(label, value, tone, display) {
    const number = numberValue(value);
    const width = number === null ? 0 : Math.max(2, Math.min(1, number) * 100);
    return '<div class="metric-row"><span class="metric-label">' + escapeHtml(label) + '</span>' +
      '<span class="metric-track"><i class="' + escapeHtml(tone || "") + '" style="--bar-width:' + width.toFixed(2) + '%"></i></span>' +
      '<b class="metric-value mono">' + escapeHtml(display == null ? ratio(number) : display) + '</b></div>';
  }

  function detailCharts(change, financial) {
    const actual = numberValue(change.actual);
    const expected = numberValue(change.expected);
    const attempted = amountValue(financial.attempted_value);
    const risk = amountValue(financial.gmv_at_risk);
    let html = '<div class="split-2 span-full detail-charts">';
    html += '<figure class="chart"><figcaption class="fc-top">Stored approval against the expected baseline. This is a comparison, not a reconstructed time series.</figcaption>';
    html += '<div class="metric-chart">' + metricBar("expected", expected, "baseline") + metricBar("actual", actual, severityClass((state.detail || {}).incident && (state.detail || {}).incident.severity)) + '</div>';
    html += '<figcaption><span class="fig">Expected and actual approval<button type="button" class="cite" data-cite="detail-actual" aria-label="cite approval comparison"></button></span> · values copied from the incident record.</figcaption></figure>';
    html += '<figure class="chart"><figcaption class="fc-top">Stored value exposure for this incident.</figcaption>';
    html += '<div class="metric-chart">' + metricBar("attempted", attempted ? 1 : null, "base", attempted ? money(attempted) : "not in store") + metricBar("at risk", attempted && risk && attempted.amount > 0 ? risk.amount / attempted.amount : null, "risk", risk ? money(risk) : "not in store") + '</div>';
    html += '<figcaption><span class="fig">Attempted value<button type="button" class="cite" data-cite="detail-attempted" aria-label="cite attempted value"></button> and GMV at risk<button type="button" class="cite" data-cite="detail-risk" aria-label="cite value exposure"></button></span> · risk is an estimate, not a platform-revenue claim.</figcaption></figure></div>';
    return html;
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
    const diagnosisPair = badgePair(
      incident.severity,
      (investigation.result || {}).diagnostic_confidence,
      incident.lifecycle_state
    );
    const diagnosis = document.createElement("article");
    diagnosis.className = "panel diagnosis-panel";
    diagnosis.innerHTML = '<h3>Diagnosis</h3><div class="dual"><div><span class="dual-l">priority</span>' +
      diagnosisPair.querySelector(".sev").outerHTML + '</div><div><span class="dual-l">confidence</span>' +
      diagnosisPair.querySelector(".conf").outerHTML + '</div></div>';
    const lost = financial.estimated_lost_approved_volume || {};
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
      readoutCell("Payments lost", fmt(lost.payments), "from estimated_lost_approved_volume", "detail-lost") +
      readoutCell("Onset", fmt(incident.onset), "from the incident record", "detail-onset") +
      readoutCell("Observed for", persistence.observed_for_seconds == null ? "not in store" : String(persistence.observed_for_seconds) + "s", "stored persistence, not a clock", "detail-persist") +
      "</dl>"
    );
    body.insertAdjacentHTML("beforeend", detailCharts(change, financial));
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
    right.appendChild(diagnosis);
    grid.appendChild(left);
    grid.appendChild(right);
    body.appendChild(grid);
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

  function blastRadiusMarkup(incident) {
    const radius = incident.blast_radius || {};
    const fields = [
      ["attempted_payments", "payments"],
      ["affected_merchants", "merchants"],
      ["affected_countries", "countries"],
      ["affected_providers", "providers"],
      ["affected_payment_methods", "payment methods"],
      ["affected_card_networks", "card networks"],
      ["affected_issuing_banks", "issuing banks"],
    ];
    const values = fields.map(function (field) { return numberValue(radius[field[0]]); });
    const max = Math.max.apply(null, values.filter(function (value) { return value !== null; }).concat([0]));
    return '<div class="br">' + fields.map(function (field, index) {
      const value = values[index];
      const width = value !== null && max > 0 ? Math.max(4, value / max * 100) : 0;
      return '<span class="br-i"><b>' + escapeHtml(value === null ? "not in store" : String(value)) + '</b><span>' +
        escapeHtml(field[1]) + '</span><i class="br-meter" style="--bar-width:' + width.toFixed(2) + '%"></i></span>';
    }).join("") + '</div>';
  }

  function escalationBinding(incident, channels) {
    const present = {};
    channels.forEach(function (event) { present[event.channel] = event.status; });
    const severity = String(incident.severity || "unknown").toLowerCase();
    const columns = ["dashboard", "slack", "phone"];
    return '<div class="binding-grid"><div class="binding-head"><span>severity</span>' + columns.map(function (channel) {
      return '<span>' + channel + '</span>';
    }).join("") + '</div><div class="binding-row ' + escapeHtml(severityClass(severity)) + '"><b>' + escapeHtml(severity) + '</b>' +
      columns.map(function (channel) {
        const status = present[channel];
        return '<span class="binding-dot ' + (status ? "on" : "") + '" title="' + escapeHtml(status || "not stored") + '">' +
          (status ? "●" : "○") + '</span>';
      }).join("") + '</div></div>';
  }

  function slackMarkup(event, incident) {
    const payload = event.payload || {};
    const change = payload.change || incident.change || {};
    const financial = payload.financial_impact || incident.financial_impact || {};
    const confidence = payload.diagnostic_confidence;
    const scope = payload.scope_label || incidentScope(incident);
    const expected = ratio(change.expected);
    const actual = ratio(change.actual);
    const risk = money(financial.gmv_at_risk);
    const burn = money(financial.loss_per_hour);
    const citations = payload.citations && typeof payload.citations === "object"
      ? Object.keys(payload.citations).map(function (key) { return payload.citations[key]; }).join(" · ")
      : "";
    return '<div class="slackish"><div class="bar mono"><span class="bar-mark">#</span><span>' +
      escapeHtml(payload.channel || "#control-tower") + '</span><span>·</span><span>' + escapeHtml(event.status || "stored") +
      '</span></div><div class="msg"><div class="av"><span class="ymark" aria-hidden="true">CW</span></div><div style="min-width:0"><div class="who">Control Tower <em>APP</em></div><div class="block ' +
      escapeHtml(severityClass(incident.severity)) + '"><p><b>' + escapeHtml(String(incident.severity || "unknown").toUpperCase()) +
      ' · approval change on ' + escapeHtml(scope) + '</b></p><p class="soft">' + escapeHtml(expected) + ' → ' + escapeHtml(actual) +
      ' · ' + escapeHtml(risk) + ' at risk · ' + escapeHtml(burn) + '/hour if sustained.</p><p class="soft">Diagnostic confidence: <b>' +
      escapeHtml(confidence || confidenceLabel(null, incident.lifecycle_state)) + '</b>. Severity and confidence are separate.</p></div>' +
      (citations ? '<div class="ctx mono">' + escapeHtml(citations) + '</div>' : '') + '</div></div></div>';
  }

  function renderEscalation() {
    const detail = state.detail;
    if (!detail) {
      escalationBoard.innerHTML = '<p class="empty">Select an incident to inspect its escalation outcomes.</p>';
      return;
    }
    const incident = detail.incident || {};
    const channels = detail.escalation || [];
    const slack = channels.filter(function (event) { return event.channel === "slack"; })[0];
    const phone = channels.filter(function (event) { return event.channel === "phone"; })[0];
    const frame = '<div class="frame"><div class="frame-bar"><span class="dot"></span><span class="path mono">/incidents/' +
      escapeHtml(incident.incident_id || "") + '/escalation</span></div><div class="frame-body">' +
      '<figure class="chart binding-chart"><figcaption class="fc-top">Stored channel outcomes for this incident. Severity routes; diagnostic confidence never does.</figcaption>' +
      escalationBinding(incident, channels) + '</figure><div class="detail-grid escalation-grid"><div><div class="panel"><h3>Slack</h3>' +
      (slack ? slackMarkup(slack, incident) : '<p class="empty">No Slack outcome is stored for this incident.</p>') +
      '</div></div><div><div class="panel"><h3>Phone</h3>' +
      '<div class="script"><p>Stored outcome: <b>' + escapeHtml(phone ? phone.status : "not in store") + '</b>.</p><p>The phone channel is a silent signal. No spoken script is stored or invented here.</p></div></div>' +
      '<div class="panel"><h3>Blast radius</h3><p class="hint">Counts copied from the incident record.</p>' + blastRadiusMarkup(incident) +
      '<p class="foot">Missing counts remain <b>not in store</b>; the surface never substitutes a guessed scope.</p></div></div></div>' +
      '<div class="panel channel-outcomes"><h3>Delivery record</h3><p class="hint">These are stored outcomes, not controls.</p><div class="chan">' +
      (channels.length ? channels.map(function (event) {
        const armed = event.status === "delivered" || event.status === "fallback_dashboard";
        return '<article class="chan-card' + (armed ? " armed" : "") + '"><h4>' + escapeHtml(event.channel || "channel") +
          '</h4><p class="state">' + escapeHtml(event.status || "not in store") + '</p></article>';
      }).join("") : '<p class="empty">No escalation outcome is stored for this incident.</p>') +
      '</div></div></div></div>';
    escalationBoard.innerHTML = frame;
    bindCites(escalationBoard);
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
    if (citeId && citeId.indexOf("queue-") === 0) {
      const keys = ["attempted_value", "gmv_at_risk", "loss_per_hour"];
      const key = keys.filter(function (name) { return citeId.indexOf("queue-" + name + "-") === 0; })[0];
      if (!key) return null;
      const incidentId = citeId.slice(("queue-" + key + "-").length);
      const item = state.queue.filter(function (entry) { return String(entry.incident_id) === incidentId; })[0];
      if (!item) return null;
      const source = item.financial_impact || {};
      const value = source[key];
      return {
        title: key.replace(/_/g, " "),
        lede: "Copied from the stored incident record. The queue did not recompute this value.",
        rows: [["source", item.incident_id], ["field", "financial_impact." + key], ["value", value || "not in store"]],
        body: value,
      };
    }
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
    const table = {
      "overview-severity": { title: "Incident severity", field: "incident.severity", value: headline.severity, source: overview.source_incident_id },
      "overview-actual": { title: "Current conversion", field: "change.actual", value: overview.current_conversion, source: overview.source_incident_id },
      "overview-expected": { title: "Expected conversion", field: "change.expected", value: overview.expected_conversion, source: overview.source_incident_id },
      "overview-gmv": { title: "GMV", field: "financial_impact.attempted_value", value: overview.gmv, source: overview.source_incident_id },
      "overview-risk": { title: "GMV at risk", field: "financial_impact.gmv_at_risk", value: overview.estimated_gmv_at_risk, source: overview.source_incident_id },
      "overview-burn": { title: "Costing / hour", field: "financial_impact.loss_per_hour", value: (overview.financial_impact || {}).loss_per_hour, source: overview.source_incident_id },
      "detail-actual": { title: "Approval now", field: "change.actual", value: change.actual, source: incident.incident_id },
      "detail-attempted": { title: "Attempted value", field: "financial_impact.attempted_value", value: financial.attempted_value, source: incident.incident_id },
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

  function refresh() {
    return jsonGet("/api/overview").then(function (overview) {
      state.overview = overview;
      state.queue = overview.incidents || [];
      const selectedStillOpen = state.queue.some(function (item) { return item.incident_id === state.selectedId; });
      if (!selectedStillOpen) state.selectedId = state.queue.length ? state.queue[0].incident_id : null;
      renderOverview();
      renderQueue();
      if (state.selectedId) return loadDetail(state.selectedId);
      state.detail = null;
      renderDetail();
      renderEscalation();
      renderEvidence();
    }).catch(function (err) {
      judgeStatus.textContent = "Store read failed. " + (err && err.error ? err.error : "Retrying.");
    });
  }

  function loadDetail(incidentId) {
    return jsonGet("/api/incidents/" + encodeURIComponent(incidentId)).then(function (detail) {
      state.detail = detail;
      renderDetail();
      renderEscalation();
      renderEvidence();
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
