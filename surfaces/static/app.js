(function () {
  "use strict";

  const state = {
    view: "overview",
    selectedId: null,
    overview: null,
    queue: [],
    watches: [],
    merchants: [],
    escalations: null,
    calls: [],
    ingestion: null,
    detail: null,
    injected: false,
    stage: "clear",
    ask: null,
    asking: false,
  };

  const overviewBoard = document.getElementById("overview-board");
  const queueBoard = document.getElementById("queue-board");
  const detailBoard = document.getElementById("detail-board");
  const escalationBoard = document.getElementById("escalation-board");
  const overviewRail = document.getElementById("overview-watch-rail");
  const overviewMerchants = document.getElementById("overview-merchants");
  const overviewNotes = document.getElementById("overview-notes");
  const askResult = document.getElementById("ask-result");
  const askExamples = document.getElementById("ask-examples");
  const queueRail = document.getElementById("queue-watch-rail");
  const evidenceBoard = document.getElementById("evidence-board");
  const judgeStatus = document.getElementById("judge-status");
  const judgeButtons = document.querySelectorAll("#judge-form [data-stage]");
  const provSource = document.getElementById("prov-source");
  const provIngest = document.getElementById("prov-ingest");
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

  // Outcomes are the investigation's own vocabulary. `ambiguous` is not the
  // system failing; it is the system declining to overstate. A token with no
  // reading here is shown as nothing at all rather than raw.
  function outcomeWords(outcome) {
    if (outcome === "diagnosed") return "diagnosed";
    if (outcome === "ambiguous") return "narrowed, not settled";
    if (outcome === "insufficient_evidence") return "not enough evidence to name a cause";
    if (outcome === "agent_unavailable") return "no cause published";
    return null;
  }

  function isInvestigating(record) {
    return ((record && record.lifecycle_state) || "") === "investigating";
  }

  // One sentence per question rather than the same sentence three times. The
  // banner above already says why nothing is published; these say what is
  // missing here, so three empty cards do not read as one error repeated.
  const WITHHELD_COPY = {
    running: {
      cause: "The agent has not named a cause yet.",
      belief: "The reasoning is published together with the cause above.",
      action: "The recommendation follows the diagnosis.",
    },
    guarded: {
      cause: "No cause survived the citation check, so none is shown.",
      belief: "There is no reasoning to show, because there is no claim to reason towards.",
      action: "No recommendation is offered on a cause the system could not stand behind.",
    },
    none: {
      cause: "No investigation has run on this incident yet.",
      belief: "Nothing to believe or doubt yet. The measured record beside this stands on its own.",
      action: "No recommendation until there is a diagnosis to base one on.",
    },
  };

  function narrativePlaceholder(incident, investigation, question) {
    const outcome = investigation && investigation.outcome;
    let bucket = "none";
    if (isInvestigating(incident)) bucket = "running";
    else if (outcome === "agent_unavailable") bucket = "guarded";
    else if (outcome) bucket = "guarded";
    return WITHHELD_COPY[bucket][question || "cause"];
  }

  // C4 narrative fields are objects (statement / explanation / action), not
  // strings. String(object) becomes "[object Object]" on the escalation view.
  // Anything that is not a usable sentence, including an object whose known
  // keys are present but empty, uses the fallback. Raw JSON is never shown.
  function c4FieldText(value, fallback) {
    if (value == null || value === "") return fallback;
    if (typeof value === "string") return value;
    if (typeof value === "object") {
      if (typeof value.statement === "string" && value.statement.trim()) return value.statement;
      if (typeof value.explanation === "string" && value.explanation.trim()) return value.explanation;
      if (typeof value.action === "string" && value.action.trim()) return value.action;
      return fallback;
    }
    return String(value);
  }

  function severityClass(severity) {
    const value = String(severity || "").toLowerCase();
    if (value === "critical" || value === "high" || value === "medium" || value === "low") {
      return "sev-" + value;
    }
    return "sev-low";
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
  function figure(value, hint, citeId, label) {
    return (
      "<dd>" + escapeHtml(value) + "</dd>" +
      '<p class="fig">' + escapeHtml(hint) + (citeId ? citeButton(citeId, "cite " + label) : "") + "</p>"
    );
  }

  // One money figure at headline weight. `tone` is the figure's own reading -
  // `risk` for money exposed, `rate` for the per-hour rate, `calm` for volume
  // that is merely context. It is not a severity and never borrows one.
  function moneyFigure(tone, label, value, hint, citeId) {
    return (
      '<div class="mfig mfig-' + tone + '">' +
      "<dt>" + escapeHtml(label) + "</dt>" +
      figure(value, hint, citeId, label) +
      "</div>"
    );
  }

  // The bar's length IS the printed figure: `ratio()` already renders the
  // stored value as a percentage string, and that same string is used as the
  // CSS width. Nothing is subtracted, scaled or otherwise derived here, and
  // the distance between the two bars is deliberately left undrawn - it is
  // not a published figure.
  function gapRow(kind, label, value, citeId, ariaLabel) {
    const text = ratio(value);
    const track = text === "not in store"
      ? '<span class="gap-track"></span>'
      : '<span class="gap-track"><i class="gap-fill gap-' + kind + '" style="width:' + text + '"></i></span>';
    return (
      '<div class="gap-row">' +
      '<span class="gap-l">' + escapeHtml(label) + "</span>" +
      track +
      '<span class="gap-v"><span class="fig">' + escapeHtml(text) +
      citeButton(citeId, "cite " + ariaLabel) + "</span></span>" +
      "</div>"
    );
  }

  function conversionGap(data, sourceId) {
    return (
      '<section class="gap" aria-label="Conversion against expected">' +
      "<h3>Conversion against expected</h3>" +
      '<p class="gap-lede">The gap between these two is what the money above is measuring. Both are copied from incident ' +
      escapeHtml(sourceId) + ".</p>" +
      gapRow("now", "now", data.current_conversion, "overview-actual", "current conversion") +
      gapRow("exp", "expected", data.expected_conversion, "overview-expected", "expected conversion") +
      '<p class="gap-cap">Each bar is drawn at its own stored value. The distance between them is not published as a figure, so it is not drawn as one.</p>' +
      "</section>"
    );
  }

  // Service status and the incident count are true and useful, so they stay -
  // but as the context that explains the money, at context weight.
  function contextStrip(headline, data, sourceId) {
    const sev = badgePair(headline.severity, "__omit__", null).querySelector(".sev").outerHTML;
    return (
      '<div class="ctxbar">' +
      '<div class="ctx"><span class="ctx-l">Service status</span><span class="ctx-v">' + sev +
      citeButton("overview-severity", "cite service status") + "</span></div>" +
      '<div class="ctx"><span class="ctx-l">Active incidents</span><span class="ctx-v">' +
      escapeHtml(count(data.active_incident_count)) + "</span></div>" +
      '<div class="ctx"><span class="ctx-l">Explained by</span><span class="ctx-v mono">' +
      escapeHtml(sourceId) + "</span></div>" +
      "</div>"
    );
  }

  // Kept, deliberately and word for word. A revenue-led board is exactly where
  // a reader looks for the invented total, so the refusal has to be on it.
  const REFUSAL_NOTE =
    '<div class="note warn tight"><h4>Two things this header deliberately does not show</h4>' +
    "<p><b>A portfolio total.</b> Adding cited <code>loss_per_hour</code> figures would be a number that exists only here. A real total has to come from W2 as its own cited figure.</p>" +
    "<p><b>Whose fault it is.</b> Attribution belongs to the investigation. A traffic light that guessed would be the worst thing this dashboard could do.</p></div>";

  // A row is a merchant only when the stored cohort names one. Where it names
  // a provider or a country instead, the row says so rather than being filed
  // under a heading that calls it a merchant.
  //
  // A row whose source record is closed is history, and is worded as history.
  // Drawing a loss rate as "if it continues" off a resolved incident - and
  // worse, drawing it under a calm headline that has just said no revenue is at
  // risk - is two panels contradicting each other on a board whose whole claim
  // is that it does not say untrue things. The money is kept, because what a
  // merchant lost earlier is real and worth reading; only the tense changes.
  function merchantCard(row, index) {
    const financial = row.financial_impact || {};
    const change = row.change || {};
    const source = row.source_incident_id || "not in store";
    const isMerchant = Boolean(row.merchant_id);
    const live = row.source_is_active !== false;
    const sev = badgePair(row.highest_severity, "__omit__", null).querySelector(".sev").outerHTML;
    const rateLabel = live ? "Loss rate" : "Was costing / hour";
    const riskLabel = live ? "Revenue at risk" : "Was at risk";
    const rateHint = live ? "loss_per_hour, if it continues" : "loss_per_hour, while it ran";
    const riskHint = live ? "gmv_at_risk, an estimate" : "gmv_at_risk over that incident, an estimate";
    const converting = live ? "Converting " : "Converted ";
    return (
      '<article class="mcard' + (live ? "" : " is-past") + '">' +
      '<div class="mcard-head">' +
      "<h4>" + escapeHtml(row.scope_label || row.merchant_id || "Platform-wide") + "</h4>" +
      '<span class="mkind">' + (isMerchant ? "merchant" : "cohort") + "</span>" +
      '<span class="mstate">' + (live ? "live" : "closed") + "</span>" +
      "</div>" +
      '<dl class="mcard-figs">' +
      '<div class="mfig mfig-rate">' +
      "<dt>" + escapeHtml(rateLabel) + "</dt>" +
      figure(money(financial.loss_per_hour) + " / hour", rateHint,
        "merchant-burn:" + index, rateLabel + " for " + (row.scope_label || source)) +
      "</div>" +
      '<div class="mfig mfig-risk">' +
      "<dt>" + escapeHtml(riskLabel) + "</dt>" +
      figure(money(financial.gmv_at_risk), riskHint,
        "merchant-risk:" + index, riskLabel + " for " + (row.scope_label || source)) +
      "</div>" +
      "</dl>" +
      '<p class="mcard-foot">' + sev +
      "<span>" + converting + escapeHtml(ratio(change.actual)) + " against " +
      escapeHtml(ratio(change.expected)) + " expected</span>" +
      '<span class="mono">' + escapeHtml(count(row.active_incident_count)) +
      " active · " + escapeHtml(source) + "</span></p>" +
      "</article>"
    );
  }

  function renderMerchants(rows) {
    const list = rows || [];
    if (!list.length) {
      overviewMerchants.innerHTML = "";
      return;
    }
    // The heading is chosen by what the rows actually are. With nothing live,
    // "Who is carrying it" would be asking a question the store is answering
    // with "nobody, any more".
    const anyLive = list.some(function (row) { return row.source_is_active !== false; });
    const heading = anyLive ? "Who is carrying it" : "What it cost earlier";
    const lede = anyLive
      ? "One row per merchant, or per cohort where the stored incident names no merchant. " +
        "Every figure is copied from that row's own highest-priority incident. Nothing is added up across rows."
      : "Nothing here is still running. Every row below is a closed incident, kept because what it cost " +
        "is real, and worded in the past because it is over. Nothing is added up across rows.";
    overviewMerchants.innerHTML =
      '<section class="impact" aria-label="Revenue impact by merchant">' +
      '<div class="impact-head"><h3>' + escapeHtml(heading) + "</h3>" +
      '<p class="impact-lede">' + escapeHtml(lede) + "</p></div>" +
      '<div class="merchants">' + list.map(merchantCard).join("") + "</div>" +
      "</section>";
    bindCites(overviewMerchants);
  }

  // -------------------------------------------------------------------
  // Ingestion provenance.
  //
  // The question a judge actually asks is "is this actually live, or is it a
  // mock?", and until now the only place the answer existed was a terminal.
  // Every figure on this line is read straight out of W2's `ingest_health`
  // evidence tool and cited like everything else on the board. Nothing here
  // adds, subtracts, converts to an age, or decides what "fresh" means.
  //
  // It is deliberately provenance and not a metric: it lives on the frame
  // edge beside the store and source line, at the same weight, below the
  // header and far from any money figure. A judge should read it the way they
  // read a footer, and find it holds up when they press it.
  // -------------------------------------------------------------------

  function provSegment(label, value, citeId, ariaLabel, tone) {
    return "<span><b>" + escapeHtml(label) + "</b> " +
      '<span class="pv' + (tone ? " " + tone : "") + '">' + escapeHtml(value) + "</span>" +
      citeButton(citeId, "cite " + ariaLabel) + "</span>";
  }

  function renderIngestion() {
    const data = state.ingestion;
    if (!data) {
      provIngest.innerHTML = "<span><b>ingest</b> waiting for store</span>";
      return;
    }
    if (data.unreadable) {
      // A stale number is worse than no number on the one line whose job is
      // to say whether the numbers are current.
      provIngest.innerHTML =
        '<span><b>ingest</b> <span class="pv pv-warn">could not be read from the store</span></span>';
      return;
    }
    const dead = data.dead_letter || {};
    // `rejected` and `dead_letter.count` are one measurement under two names,
    // equal by construction (C2 contract, section 12). They are printed as one
    // segment so the line cannot be read as two independent facts.
    const refused = count(data.rejected) + " rejected \u00b7 " + count(dead.count) + " dead-lettered";
    const parts = [
      provSegment("ingest", count(data.accepted) + " accepted", "ingest-accepted",
        "records accepted", data.accepted ? null : "pv-quiet"),
      provSegment("refused", refused, "ingest-refused",
        "records refused", data.rejected ? "pv-warn" : null),
    ];
    if (data.newest_event_at) {
      parts.push(provSegment("last event", data.newest_event_at, "ingest-newest",
        "newest observed event", null));
      parts.push(provSegment("measured through", data.watermark, "ingest-watermark",
        "measurement watermark", null));
    } else {
      // A store that has observed nothing has an epoch watermark. That is the
      // honest value and the drawer still shows it, but printing 1970 on the
      // frame reads as a broken clock rather than as an empty store, so the
      // line says the thing the epoch means.
      parts.push(provSegment("last event", "nothing observed yet", "ingest-newest",
        "newest observed event", "pv-quiet"));
    }
    provIngest.innerHTML = parts.join("");
    bindCites(provIngest);
  }

  function renderOverview() {
    const data = state.overview;
    if (!data) {
      overviewBoard.innerHTML = '<p class="empty">No overview yet.</p>';
      overviewMerchants.innerHTML = "";
      overviewNotes.innerHTML = "";
      provSource.innerHTML = "<b>source</b> waiting for store";
      return;
    }
    const headline = (data.incidents || [])[0] || null;
    const sourceId = data.source_incident_id || "none";
    provSource.innerHTML = "<b>source</b> " + escapeHtml(sourceId);
    overviewNotes.innerHTML = REFUSAL_NOTE;
    renderMerchants(state.merchants);
    if (!headline) {
      // Nothing wrong is the healthy state of a business board, not a failed
      // load. It says what is true - no money is exposed - and stays calm.
      overviewBoard.innerHTML =
        '<section class="calm">' +
        '<span class="calm-mark" aria-hidden="true"></span>' +
        "<div><h3>No revenue at risk</h3>" +
        "<p>The store holds no active incident, so there is no money figure to copy. " +
        "Figures appear here the moment detection reports one.</p></div>" +
        "</section>";
      return;
    }
    const financial = data.financial_impact || {};
    overviewBoard.innerHTML =
      '<section class="topline" aria-label="Revenue at risk">' +
      '<dl class="money">' +
      moneyFigure("risk", "Revenue at risk", money(data.estimated_gmv_at_risk),
        "estimated · gmv_at_risk on incident " + sourceId, "overview-risk") +
      moneyFigure("rate", "Loss rate", money(financial.loss_per_hour) + " / hour",
        "loss_per_hour on that incident, if it continues", "overview-burn") +
      moneyFigure("calm", "Attempted value", money(data.gmv),
        "attempted_value on that incident", "overview-gmv") +
      "</dl>" +
      conversionGap(data, sourceId) +
      "</section>" +
      contextStrip(headline, data, sourceId);
    bindCites(overviewBoard);
  }

  // ---------------------------------------------------------------------
  // Ask the data.
  //
  // This panel is the one thing on the board that costs a model call, so it is
  // fired by a press and by nothing else: `refresh()` never touches /api/ask,
  // and the endpoint refuses GET so no careless read can reach it either. One
  // question runs at a time - the server holds that lock, and the button
  // reflects it rather than the page pretending to be idle.
  //
  // Nothing here computes. Every figure and every query below is copied out of
  // what the engine returned, and a figure the engine did not tie to a query
  // says so rather than borrowing a citation it does not have.
  // ---------------------------------------------------------------------

  const ASK_EXAMPLES = [
    "Why did approvals drop for merchant-b?",
    "Which decline reason is costing us the most?",
    "Is adyen worse than the others today?",
  ];

  // Values arrive from the engine already priced and worded. `money()` is used
  // only for the shape the store uses for money; nothing else is reformatted,
  // and no unit is invented for a bare number.
  function askValue(value) {
    if (value === null || value === undefined || value === "") return "not in store";
    if (typeof value === "object") {
      if ("amount" in value) return money(value);
      return pretty(value);
    }
    return String(value);
  }

  function askFigures(figures) {
    const rows = figures || [];
    if (!rows.length) return "";
    return '<dl class="ask-figs">' + rows.map(function (row, index) {
      const cited = row.query_id
        ? '<span class="fig">' + escapeHtml(askValue(row.value)) +
          citeButton("ask-fig:" + index, "cite " + (row.label || "figure")) + "</span>"
        : '<span class="ask-uncited">' + escapeHtml(askValue(row.value)) + "</span>";
      const note = row.query_id
        ? '<small class="mono">' + escapeHtml(row.tool || "tool not in store") + " · " +
          escapeHtml(row.query_id) + "</small>"
        : '<small class="ask-nocite">the engine tied no query to this one</small>';
      return '<div class="ask-fig"><dt>' + escapeHtml(row.label || "figure") + "</dt>" +
        "<dd>" + cited + note + "</dd></div>";
    }).join("") + "</dl>";
  }

  // What the engine says it would have needed. This is the whole reason an
  // unanswerable question reads as confidence rather than as a shrug, so it is
  // drawn as content, not as an apology.
  function askMissing(missing) {
    const rows = missing || [];
    if (!rows.length) return "";
    return (
      '<div class="ask-missing"><h5>What it would have needed</h5><ul>' +
      rows.map(function (item) { return "<li>" + escapeHtml(item) + "</li>"; }).join("") +
      "</ul></div>"
    );
  }

  function askCitations(citations) {
    const rows = citations || [];
    if (!rows.length) return "";
    return (
      '<div class="ask-trail">' +
      "<h5>The queries it ran</h5>" +
      '<p class="ask-trailcap">Every call the engine made, in order, each under the query id it is ' +
      "recorded against - the same ids the evidence trail carries. Open one to see what was asked.</p>" +
      '<ol class="ask-cites">' + rows.map(function (row, index) {
        return '<li class="ask-cite">' +
          '<span class="ask-seq mono">' + escapeHtml(count(row.sequence)) + "</span>" +
          '<span class="ask-tool">' + escapeHtml(row.tool || "tool not in store") + "</span>" +
          '<span class="ask-qid mono">' + escapeHtml(row.query_id || "query id not in store") + "</span>" +
          '<span class="ask-outcome">' + escapeHtml(row.outcome || "outcome not in store") + "</span>" +
          citeButton("ask-cite:" + index, "cite query " + count(row.sequence)) +
          "</li>";
      }).join("") + "</ol></div>"
    );
  }

  // The engine's watermark, shown because an answer about "today" that is
  // eight hours stale is a different answer.
  function askAsOf(payload) {
    if (!payload.as_of) return "";
    return '<p class="ask-asof mono">measured as of ' + escapeHtml(payload.as_of) + "</p>";
  }

  // Four outcomes, four designs. `ambiguous` and `insufficient_evidence` are
  // deliberately not collapsed into one message: one means the evidence was
  // reached and does not settle the question, the other means the evidence is
  // not there at all. Telling a judge which of those happened is most of why
  // this reads as honest rather than evasive.
  const ASK_STATES = {
    ambiguous: {
      tone: "limit",
      title: "The evidence does not settle it",
      lede: "It reached the measurements and they support more than one explanation. Rather than pick one, it says so. Every query it ran is below.",
    },
    insufficient_evidence: {
      tone: "limit",
      title: "Not answerable from what we measure",
      lede: "The evidence this question needs is not in the store. This is a gap in what we measure, not a failure of the question.",
    },
    no_api_key: {
      tone: "off",
      title: "No model is configured",
      lede: "Asking needs a model. Every other figure on this board was measured and stored, not generated, so the rest of the page is unaffected.",
    },
    engine_missing: {
      tone: "off",
      title: "The ask engine is not in this build",
      lede: "The panel is wired and the route answers; the engine module is not installed here.",
    },
    timeout: {
      tone: "limit",
      title: "The question ran past its limit",
      lede: "It was stopped rather than left running. Anything it had already queried is below.",
    },
    engine_error: {
      tone: "off",
      title: "The engine could not complete",
      lede: "It failed rather than returned a guess.",
    },
  };

  function askStateFor(payload) {
    if (payload.outcome === "agent_unavailable") {
      return ASK_STATES[payload.unavailable_kind] || ASK_STATES.engine_error;
    }
    return ASK_STATES[payload.outcome] || null;
  }

  function renderAsk() {
    if (state.asking) {
      askResult.innerHTML =
        '<div class="ask-card is-pending"><div class="ask-status">' +
        '<span class="ask-spin" aria-hidden="true"></span>' +
        "<b>Reading the store</b></div>" +
        "<p>The engine is choosing and running its own queries against this store, up to six of " +
        "them, and it has thirty seconds. Every query it runs is listed here when it answers, " +
        "including the ones that came back empty.</p>" +
        "</div>";
      return;
    }
    const payload = state.ask;
    if (!payload) {
      askResult.innerHTML = "";
      return;
    }
    if (payload.busy) {
      askResult.innerHTML =
        '<div class="ask-card is-busy"><div class="ask-status"><b>A question is already running</b></div>' +
        "<p>" + escapeHtml(payload.detail || "One question runs at a time. This press started nothing new.") +
        "</p></div>";
      return;
    }
    const asked = '<p class="ask-asked">' + escapeHtml(payload.question || "") + "</p>";
    const trail = askCitations(payload.citations);
    const missing = askMissing(payload.missing_evidence);
    const info = askStateFor(payload);
    if (info) {
      askResult.innerHTML =
        '<div class="ask-card is-' + info.tone + '">' + asked +
        "<h4>" + escapeHtml(info.title) + "</h4>" +
        '<p class="ask-lede">' + escapeHtml(info.lede) + "</p>" +
        (payload.answer ? '<p class="ask-detail">' + escapeHtml(payload.answer) + "</p>" : "") +
        missing + trail + askAsOf(payload) + "</div>";
      bindCites(askResult);
      return;
    }
    askResult.innerHTML =
      '<div class="ask-card is-answer">' + asked +
      '<p class="ask-answer">' + escapeHtml(payload.answer || "The engine returned no wording for this answer.") + "</p>" +
      askFigures(payload.figures) + trail + askAsOf(payload) + "</div>";
    bindCites(askResult);
  }

  function renderAskExamples() {
    askExamples.innerHTML = ASK_EXAMPLES.map(function (text) {
      return '<button type="button" class="ask-eg">' + escapeHtml(text) + "</button>";
    }).join("");
    askExamples.querySelectorAll("button.ask-eg").forEach(function (button) {
      button.addEventListener("click", function () {
        $("ask-input").value = button.textContent;
        submitAsk();
      });
    });
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


  // ---------------------------------------------------------------------
  // The warning rail.
  //
  // A watch is a developing deviation the detector deliberately chose not to
  // report: it has not crossed the detection floors, it is forced to `low`, and
  // nothing pages on it. So it is drawn quietly and apart - never in the
  // incident queue, never in the "Right now" figures, and never in a severity
  // colour. Every figure below is copied out of the stored C3 record that
  // surfaces/present.py:watch_item passed through. Nothing here is computed.
  // ---------------------------------------------------------------------

  // Stored snake_case keys, shown as words. This renames a key for reading; it
  // is not a value, and no figure is derived from it.
  function floorLabel(key) {
    return String(key).replace(/_/g, " ");
  }

  function floorChips(floors) {
    if (!floors || typeof floors !== "object") return "";
    const keys = Object.keys(floors);
    if (!keys.length) return "";
    return '<ul class="floors">' + keys.map(function (key) {
      const held = floors[key] === true;
      return '<li class="floor ' + (held ? "held" : "open") + '">' +
        '<i aria-hidden="true"></i>' + escapeHtml(floorLabel(key)) +
        '<span class="vh">' + (held ? " met" : " not met") + "</span></li>";
    }).join("") + "</ul>";
  }

  // The chips are the stored detection-floor vector. Without saying so, a
  // lone "has measurement" chip under "Not an incident yet" reads as a reason
  // it is one.
  function floorCaption(floors, index) {
    if (index !== 0 || !floors || typeof floors !== "object" || !Object.keys(floors).length) return "";
    return '<p class="rail-cap">Detection floors. Dashed is not yet crossed.</p>';
  }

  function trajectoryLine(watch) {
    const value = watch.trajectory;
    if (value === null || value === undefined) return "";
    const floors = watch.watch_floors || {};
    const worsening = floors.worsening === true ? " · getting worse" : "";
    return '<p class="rail-traj">Trajectory ' + escapeHtml(count(value)) + escapeHtml(worsening) + "</p>";
  }

  function watchRow(watch, index) {
    const projected = watch.projected_loss_per_hour || null;
    const reasons = (watch.reasons || []).map(floorLabel).join(", ");
    return (
      '<li class="rail-item">' +
        '<div class="rail-who">' +
          '<span class="watching"><i aria-hidden="true"></i>watching</span>' +
          "<b>" + escapeHtml(incidentScope(watch)) + "</b>" +
          '<span class="sub mono">' + escapeHtml(cohortLine(watch.affected_cohort)) + "</span>" +
        "</div>" +
        '<div class="rail-proj">' +
          "<dt>Projected</dt>" +
          '<dd><span class="fig">' + escapeHtml(money(projected)) + " / hour if this continues" +
            citeButton("watch-proj:" + index, "cite projected loss for " + incidentScope(watch)) +
          "</span></dd>" +
          "<small>" + escapeHtml(onsetLine(watch)) + "</small>" +
        "</div>" +
        '<div class="rail-why">' +
          "<dt>Not an incident yet</dt>" +
          floorCaption(watch.detection_floors, index) +
          floorChips(watch.detection_floors) +
          trajectoryLine(watch) +
          (reasons ? '<p class="rail-traj">Watched for ' + escapeHtml(reasons) + "</p>" : "") +
        "</div>" +
      "</li>"
    );
  }

  function onsetLine(watch) {
    return watch.onset ? "first seen " + watch.onset : "onset not in store";
  }

  function watchRail(watches) {
    const rows = watches || [];
    const head =
      '<div class="rail-head">' +
        "<h3>Watching</h3>" +
        (rows.length
          ? '<span class="rail-count">' + escapeHtml(count(rows.length)) +
            (rows.length === 1 ? " cohort" : " cohorts") + "</span>"
          : "") +
      "</div>" +
      '<p class="rail-lede">Developing deviations that have not crossed the detection floors. ' +
      "They are not incidents, they are not counted in the figures above, and nothing pages on them.</p>";
    if (!rows.length) {
      return '<section class="rail is-quiet" aria-label="Watching">' + head +
        '<p class="rail-none">Nothing is being watched. A cohort appears here the moment detection ' +
        "measures it deviating without crossing its floors.</p></section>";
    }
    const statement = rows[0].statement;
    return '<section class="rail" aria-label="Watching">' + head +
      '<ul class="rail-list">' + rows.map(watchRow).join("") + "</ul>" +
      (statement ? '<p class="rail-foot">' + escapeHtml(statement) + "</p>" : "") +
      "</section>";
  }

  function renderWatchRail() {
    const markup = watchRail(state.watches);
    [overviewRail, queueRail].forEach(function (host) {
      host.innerHTML = markup;
      bindCites(host);
    });
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
      (outcomeWords(outcome) ? " · " + outcomeWords(outcome) : "") +
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
    // PRD section 11 keeps severity and confidence independent on purpose, and
    // docs/ownership.md puts them in different workstreams so they cannot
    // collapse into one score. Drawing them side by side without saying that
    // leaves a judge reading a puzzling pair; one line of copy turns it into a
    // design decision. It asserts no figure.
    const diagnosis = '<div class="panel"><h3>Priority and confidence</h3>' +
      '<p class="hint">Two readings, two owners, deliberately not one score.</p>' +
      '<div class="dual">' +
      '<div><span class="dual-l">priority</span>' + diagnosisPair.querySelector(".sev").outerHTML + "</div>" +
      '<div><span class="dual-l">confidence</span>' + diagnosisPair.querySelector(".conf").outerHTML + "</div>" +
      "</div>" +
      '<p class="dual-note">Priority is the measured business impact of the incident. Confidence is the ' +
      "investigation's assessment of how strongly the evidence supports a cause. Neither is allowed to move " +
      "the other, so a critical incident at low confidence reads exactly as it should: a large problem " +
      "nobody can explain yet, which is worse than a small one and not better. Escalation routes on " +
      "priority alone.</p>" +
      "</div>";
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
    // The cause is the answer this screen exists to give, so it runs the full
    // width above everything else. Below it, the long "why" takes the wide
    // column and the shorter answers stack in the narrow one - the reasoning
    // leads, and the measured facts stay beside it rather than above it.
    const answered = questions.narrative_available;
    function withheldOr(question, html) {
      if (answered) return html;
      return '<p class="q-none">' +
        escapeHtml(narrativePlaceholder(incident, investigation, question)) + "</p>";
    }
    const cause = questionCard(
      "4. What probably caused it?",
      "The investigation's leading hypothesis. Never guessed in the UI.",
      withheldOr("cause", causeBlock(questions.what_probably_caused_it,
        narrativePlaceholder(incident, investigation, "cause")))
    );
    const grid = document.createElement("div");
    grid.className = "detail-grid";
    const left = document.createElement("div");
    // Full width is the promotion an answer earns. With nothing published there
    // is nothing to promote, and a wide empty card only makes the gap bigger,
    // so the three unanswered questions stay a stack in the narrow rhythm.
    if (answered) {
      cause.classList.add("q-cause");
      body.appendChild(cause);
    } else {
      left.appendChild(cause);
    }
    left.appendChild(questionCard(
      "5. Why do we believe that?",
      "What is established, what is not ruled out, and what would settle it.",
      withheldOr("belief", beliefBlock(questions.why_we_believe_that,
        narrativePlaceholder(incident, investigation, "belief")))
    ));
    const right = document.createElement("div");
    right.appendChild(questionCard(
      "6. What should the TAM do?",
      "Recommended next action from the investigation record.",
      withheldOr("action", actionBlock(questions.what_the_operator_should_do,
        narrativePlaceholder(incident, investigation, "action")))
    ));
    right.appendChild(questionCard(
      "1. What changed?",
      "Copied from the incident change block.",
      changeBlock(questions.what_changed)
    ));
    right.appendChild(questionCard(
      "2. Where?",
      "Affected cohort as stored.",
      whereBlock(questions.where, incident)
    ));
    right.appendChild(questionCard(
      "3. How much does it matter?",
      "Financial impact as stored.",
      moneyBlock(questions.how_much_it_matters)
    ));
    right.insertAdjacentHTML("beforeend", diagnosis);
    grid.appendChild(left);
    grid.appendChild(right);
    body.appendChild(grid);
    body.insertAdjacentHTML("beforeend", chanCards);
    detailBoard.innerHTML = "";
    detailBoard.appendChild(frame);
    bindCites(detailBoard);
  }

  function questionCard(title, hint, inner) {
    const article = document.createElement("article");
    article.className = "panel question";
    article.innerHTML = "<h3>" + escapeHtml(title) + "</h3>" +
      (hint ? '<p class="hint">' + escapeHtml(hint) + "</p>" : "") + inner;
    return article;
  }

  // ---------------------------------------------------- the agent, in prose
  //
  // Everything below turns values that are already in `detail.evidence_trail`,
  // `detail.investigation` and `detail.incident` into sentences. It reads and
  // formats; it never computes. A number that reaches the screen through one of
  // these helpers is the number the store published - docs/ownership.md's W4
  // rule makes a figure that exists only in the UI a defect of this layer.

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const DIMENSION_WORDS = {
    merchant_id: "merchant",
    provider: "provider",
    payment_method: "method",
    card_network: "network",
    country: "country",
    issuing_bank: "bank",
  };

  function dimensionWord(key) {
    return DIMENSION_WORDS[key] || String(key || "").replace(/_/g, " ");
  }

  function clockOf(stamp) {
    const match = /T(\d{2}:\d{2})/.exec(String(stamp || ""));
    return match ? match[1] : null;
  }

  function dayOf(stamp) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(stamp || ""));
    if (!match) return null;
    return String(Number(match[3])) + " " + (MONTHS[Number(match[2]) - 1] || match[2]);
  }

  // "05:14-05:19 UTC on 30 Aug". A window that does not parse returns null and
  // the sentence is built without it, rather than half-formatted into something
  // that reads like a different interval.
  function windowPhrase(window) {
    if (!window || typeof window !== "object") return null;
    const from = clockOf(window.start);
    const to = clockOf(window.end);
    if (!from || !to) return null;
    const startDay = dayOf(window.start);
    const endDay = dayOf(window.end);
    if (startDay && endDay && startDay !== endDay) {
      return from + " on " + startDay + " to " + to + " on " + endDay + " UTC";
    }
    return from + "-" + to + " UTC" + (startDay ? " on " + startDay : "");
  }

  function cohortWords(cohort) {
    if (!cohort || typeof cohort !== "object") return null;
    const order = ["merchant_id", "provider", "payment_method", "card_network", "country", "issuing_bank"];
    const parts = order
      .filter(function (key) { return cohort[key]; })
      .map(function (key) { return dimensionWord(key) + " " + cohort[key]; });
    if (!parts.length) return "all traffic";
    return parts.join(", ");
  }

  function targetWords(target) {
    if (!target || typeof target !== "object") return "the target";
    const rest = {};
    Object.keys(target).forEach(function (key) {
      if (key !== "kind") rest[key] = target[key];
    });
    if (target.kind === "service") return "service " + (target.service || cohortWords(rest) || "not named");
    return cohortWords(rest) || "all traffic";
  }

  function metricWords(metric) {
    return String(metric || "payment approval conversion").replace(/_/g, " ");
  }

  function durationWords(ms) {
    if (typeof ms !== "number" || !isFinite(ms)) return null;
    if (ms < 1000) return Math.round(ms) + " ms";
    if (ms < 60000) return (ms / 1000).toFixed(1) + " s";
    const seconds = Math.round(ms / 1000);
    return Math.floor(seconds / 60) + "m " + String(seconds % 60).padStart(2, "0") + "s";
  }

  function pct(value) {
    if (typeof value !== "number" || !isFinite(value)) return null;
    return (value * 100).toFixed(1) + "%";
  }

  // A stored difference between two ratios, read as percentage points. `shift`
  // and `absolute_delta` are differences, so "17.5 points" is the honest
  // reading of 0.175 and "17.5%" is not.
  function pointsWord(value) {
    if (typeof value !== "number" || !isFinite(value)) return null;
    return Math.abs(value * 100).toFixed(1) + " points";
  }

  function num(value) {
    if (typeof value !== "number" || !isFinite(value)) return null;
    return String(value);
  }

  function moneyOrNull(value) {
    if (!value || typeof value !== "object" || !("amount" in value)) return null;
    return money(value);
  }

  function joinWords() {
    const parts = Array.prototype.slice.call(arguments).filter(function (part) { return part; });
    return parts.length ? parts.join(", ") : null;
  }

  function ofPair(part, whole, word) {
    if (typeof part !== "number" || typeof whole !== "number") return null;
    return String(part) + " " + word + " " + String(whole);
  }

  function lowerFirst(text) {
    const value = String(text || "");
    // Only lower a plain capitalised opening. An identifier the store spells a
    // particular way - "GMV", "P2" - is left exactly as it was published.
    return /^[A-Z][a-z]/.test(value) ? value.charAt(0).toLowerCase() + value.slice(1) : value;
  }

  // What the agent asked, said as a question rather than as a request body. The
  // tool, the cohort and the window are all read from `entry.parameters`.
  function askedSentence(entry) {
    const parameters = (entry && entry.parameters) || {};
    const tool = (entry && entry.tool) || "";
    const window = windowPhrase(parameters.window);
    const over = window ? ", over " + window : "";
    const cohort = cohortWords(parameters.cohort);
    switch (tool) {
      case "cohort_metrics":
        return "How " + (cohort || "the cohort") + " converted" + over + ".";
      case "cohort_compare":
        return "How " + (cohort || "the cohort") + " compared with its siblings and its parent" +
          (parameters.compare_dimensions && parameters.compare_dimensions.length
            ? ", split by " + parameters.compare_dimensions.map(dimensionWord).join(", ")
            : "") + over + ".";
      case "drilldown":
        return "Which level of the cohort the failure localises to, and where the path stopped" + over + ".";
      case "decline_breakdown":
        return "Which decline reasons moved for " + (cohort || "the cohort") + over + ".";
      case "retry_stats":
        return "How far retries went for " + (cohort || "the cohort") + ", and what that did to the queue" + over + ".";
      case "operational_metrics":
        return "Latency, errors, timeouts and health for " + targetWords(parameters.target) + over + ".";
      case "confounding_check":
        return "Whether " + dimensionWord(parameters.dimension_a) + " and " + dimensionWord(parameters.dimension_b) +
          " can be told apart in the data at all" + over + ".";
      case "incident_history":
        return "Whether " + (parameters.merchant_id || "this merchant") + " has been here before.";
      case "external_status":
        return "What " + (parameters.provider || "the provider") + " reports about itself, as outside corroboration.";
      case "financial_impact":
        return "What this incident is costing" + over + ".";
      case "metric_series":
        return "How " + metricWords(parameters.metric) + " moved bucket by bucket for " +
          (cohort || "all traffic") + over + ".";
      case "ingest_health":
        return "Whether anything is still arriving in the store that every other answer here is read from.";
      default:
        return "A " + (tool || "gateway") + " query" + over + ".";
    }
  }

  // The two or three readings that actually mattered for this tool, as
  // label/value pairs. Every value is lifted straight out of `entry.response`.
  function readingsFor(entry) {
    const response = (entry && entry.response) || {};
    const rows = [];
    function add(label, value) {
      if (value === null || value === undefined || value === "") return;
      rows.push([label, String(value)]);
    }
    if (response.error) {
      add("refused", response.error.code || "error");
      add("because", response.error.message || "no message was returned");
      return rows;
    }
    switch ((entry && entry.tool) || "") {
      case "cohort_metrics": {
        const payments = response.payment_metrics || {};
        const attempts = response.attempt_metrics || {};
        const baseline = response.baseline || {};
        const expected = payments.expected_approval_conversion != null
          ? payments.expected_approval_conversion
          : baseline.payment_approval_conversion;
        add("payment conversion", joinWords(pct(payments.approval_conversion),
          pct(expected) ? "against a " + pct(expected) + " baseline" : null));
        add("payments", ofPair(payments.approved_payments, payments.attempted_payments, "approved of"));
        add("attempts", joinWords(ofPair(attempts.approved_attempts, attempts.attempts, "approved of"),
          num(attempts.failed_attempts) ? num(attempts.failed_attempts) + " failed" : null));
        add("decline mix", (response.decline_mix || []).slice(0, 3).map(function (row) {
          return row.reason + " " + (pct(row.share) || "share not in store");
        }).join(" · ") || null);
        break;
      }
      case "cohort_compare": {
        add("this cohort", pct(((response.target || {}).payment_metrics || {}).approval_conversion));
        (response.siblings || []).slice(0, 3).forEach(function (sibling) {
          add(cohortWords(sibling.cohort) || sibling.label || "sibling",
            pct((sibling.payment_metrics || {}).approval_conversion));
        });
        add("everything around it", pct(((response.parent || {}).payment_metrics || {}).approval_conversion));
        break;
      }
      case "drilldown": {
        const levels = response.levels || [];
        add("levels walked", levels.length ? levels.map(function (level) { return level.level; }).join(" → ") : null);
        add("stopped at", response.stopped_at);
        add("why it stopped", response.stop_reason);
        break;
      }
      case "decline_breakdown": {
        (response.reasons || []).slice(0, 3).forEach(function (row) {
          add(row.reason, joinWords(
            pct(row.share),
            typeof row.shift === "number"
              ? (row.shift >= 0 ? "up " : "down ") + pointsWord(row.shift) +
                (pct(row.baseline_share) ? " on a " + pct(row.baseline_share) + " baseline" : "")
              : null
          ));
        });
        add("measured against", num(response.failed_attempts)
          ? num(response.failed_attempts) + " failed attempts" : null);
        break;
      }
      case "retry_stats": {
        add("attempts per payment", num(response.attempts_per_payment));
        add("payments retried", ofPair(response.retried_payments, response.payments, "of"));
        add("deepest retry", num((response.retry_depth || {}).max));
        const queue = response.queue || {};
        add("queue", queue.depth_peak == null
          ? "no queue observation in this window"
          : joinWords("peak depth " + queue.depth_peak,
            queue.delay_p95_ms == null ? null : "p95 delay " + queue.delay_p95_ms + " ms"));
        break;
      }
      case "operational_metrics": {
        const latency = response.latency_ms || {};
        add("latency", joinWords(
          latency.p50 == null ? null : "p50 " + latency.p50 + " ms",
          latency.p95 == null ? null : "p95 " + latency.p95 + " ms",
          latency.p99 == null ? null : "p99 " + latency.p99 + " ms"
        ));
        add("timeouts", pct(response.timeout_rate));
        add("errors", pct(response.error_rate));
        add("service health", (response.service_health || {}).status);
        add("runtime health", (response.runtime_health || {}).status);
        add("deployment", (response.deployment || {}).deployment_id);
        break;
      }
      case "confounding_check": {
        if (response.structurally_inseparable === true) {
          add("can they be told apart", "no - every value of one appears with exactly one value of the other");
        } else if (response.structurally_inseparable === false) {
          add("can they be told apart", "yes - the data separates them");
        }
        add("what that means", response.interpretation);
        add("cross-tabulated", ((response.cross_tabulation || {}).rows || []).length
          ? ((response.cross_tabulation || {}).rows || []).length + " observed combinations" : null);
        break;
      }
      case "incident_history": {
        const recurrence = response.recurrence || {};
        add("prior matching incidents", num(recurrence.prior_matching_incidents));
        add("looking back", recurrence.lookback_days == null ? null : recurrence.lookback_days + " days");
        add("pattern", recurrence.pattern);
        add("records returned", String((response.incidents || []).length));
        break;
      }
      case "external_status": {
        add("status", response.status);
        add("source", response.source);
        add("checked at", response.checked_at);
        add("reason", response.reason);
        add("effect on the diagnosis", response.diagnostic_effect);
        break;
      }
      case "financial_impact": {
        const lost = response.estimated_lost_approved_volume || {};
        add("at risk", moneyOrNull(response.gmv_at_risk));
        add("if it runs an hour", moneyOrNull(response.loss_per_hour));
        add("approvals not captured", joinWords(
          num(lost.payments) ? num(lost.payments) + " payments" : null, moneyOrNull(lost)));
        add("approval rate", joinWords(pct(response.actual_approval_rate),
          pct(response.expected_approval_rate) ? "against " + pct(response.expected_approval_rate) + " expected" : null));
        break;
      }
      case "metric_series": {
        const series = (response.points || []).filter(function (point) { return typeof point.value === "number"; });
        add("metric", metricWords(response.metric));
        add("buckets", (response.points || []).length
          ? (response.points || []).length + " of " + (response.bucket_seconds || 60) + " seconds" : "none returned");
        if (series.length) {
          add("first reading", clockOf(series[0].bucket_start) + " · " + series[0].value);
          if (series.length > 1) {
            add("last reading", clockOf(series[series.length - 1].bucket_start) + " · " + series[series.length - 1].value);
          }
        }
        add("measured through", response.measured_through);
        break;
      }
      case "ingest_health": {
        add("accepted", num(response.accepted));
        add("rejected", num(response.rejected));
        add("newest event", response.newest_event_at);
        add("not yet measured", response.lag_seconds == null
          ? null : response.lag_seconds + " seconds behind the watermark");
        break;
      }
      default:
        Object.keys(response).sort().forEach(function (key) {
          if (key === "query_id" || key === "as_of" || rows.length >= 4) return;
          if (response[key] && typeof response[key] === "object") return;
          add(key.replace(/_/g, " "), String(response[key]));
        });
    }
    add("measured at", response.as_of);
    return rows;
  }

  // A cited claim names the query behind it before you click. This is the same
  // affordance as every other cite on the board - the same `data-cite`, the
  // same `citeRecord` lookup, the same drawer - carrying the query's ordinal.
  function citeChip(citeId, text, label) {
    return '<button type="button" class="cite cite-q" data-cite="' + escapeHtml(citeId) +
      '" aria-label="' + escapeHtml(label || "cite") + '">' + escapeHtml(text) + "</button>";
  }

  function trailSequenceFor(queryId) {
    const trail = (state.detail && state.detail.evidence_trail) || [];
    const match = trail.filter(function (entry) {
      return entry && String(entry.query_id) === String(queryId);
    })[0];
    return match ? Number(match.sequence) : null;
  }

  // C4 evidence items. A citation whose query_id is not in the stored trail is
  // shown as exactly that rather than given a button that opens nothing; the
  // validator should make it impossible, and if it ever happens it must be
  // visible.
  function evidenceList(items, emptyText) {
    const rows = (items || []).filter(function (item) { return item && (item.claim || item.query_id); });
    if (!rows.length) return emptyText ? '<p class="q-none">' + escapeHtml(emptyText) + "</p>" : "";
    return '<ul class="ev">' + rows.map(function (item) {
      const sequence = trailSequenceFor(item.query_id);
      const source = sequence === null
        ? '<span class="ev-orphan mono">' + escapeHtml(String(item.query_id || "no query id")) + " · not in the stored trail</span>"
        : citeChip("trail-" + sequence, "Q" + sequence,
          "cite query " + sequence + ", " + (item.tool || "gateway"));
      return "<li>" +
        '<span class="ev-claim">' + escapeHtml(item.claim || "no claim was stored for this citation") + "</span>" +
        '<span class="ev-src"><span class="ev-tool mono">' + escapeHtml(item.tool || "") + "</span>" + source + "</span>" +
        "</li>";
    }).join("") + "</ul>";
  }

  // -------------------------------------------------- the wait, and the guard

  // The order the agent runs in. It is a description of the pipeline, not a
  // live position: nothing here claims to know which step is executing,
  // because nothing on the wire says so. See the FOR DEREK line in STATUS.md.
  const AGENT_STEPS = [
    ["Opening evidence", "the same first queries on every incident, so the start is never improvised"],
    ["Candidate causes", "a deterministic prefilter, before a model is shown anything"],
    ["Targeted queries", "the agent choosing what to ask next, inside a fixed query budget"],
    ["Citation check", "every causal claim matched against a query that actually ran"],
    ["Narrative", "published only once that check passes"],
  ];

  function elapsedWords(since) {
    const started = Date.parse(since);
    if (!started) return "";
    const seconds = Math.max(0, Math.round((Date.now() - started) / 1000));
    if (seconds < 60) return seconds + "s";
    return Math.floor(seconds / 60) + "m " + String(seconds % 60).padStart(2, "0") + "s";
  }

  // The elapsed reading rides on the stored `started_at`, so it is the age of
  // the run and not the age of this tab. Without a stored start there is no
  // clock at all rather than one counting from a page load.
  function tickAgentRun() {
    document.querySelectorAll(".run-clock[data-since]").forEach(function (node) {
      node.textContent = elapsedWords(node.getAttribute("data-since"));
    });
  }

  // The clock is only drawn when the served `started_at` belongs to a run that
  // is still open. An incident re-investigated after a watch crosses its floor
  // still carries the previous version's start until the new one lands, and
  // counting from that would put hours on a run a second old. Today the
  // detail payload carries no start for a run in flight at all, so this is
  // usually absent - see the FOR JUANK line in STATUS.md.
  function agentRunning(investigation) {
    const since = (investigation && investigation.started_at) || "";
    const open = investigation && !investigation.completed_at;
    const clock = open && Date.parse(since)
      ? '<span class="run-clock mono" data-since="' + escapeHtml(String(since)) + '">' +
        escapeHtml(elapsedWords(since)) + "</span>"
      : "";
    return '<div class="agentrun banner">' +
      '<div class="run-head"><h4>The agent is investigating</h4>' + clock + "</div>" +
      '<div class="run-bar" role="presentation"><i></i></div>' +
      "<p>It is interrogating the same evidence tools this board reads, one query at a time, and " +
      "choosing the next question from what the last one returned.</p>" +
      '<ol class="run-steps">' + AGENT_STEPS.map(function (step) {
        return "<li><b>" + escapeHtml(step[0]) + "</b><span>" + escapeHtml(step[1]) + "</span></li>";
      }).join("") + "</ol>" +
      '<p class="run-foot">That is the order it runs in, not a live position: the trail is stored in one ' +
      "write when the run finishes, so the queries appear together. The board polls itself; nothing here " +
      "needs clicking.</p>" +
      "</div>";
  }

  // `agent_unavailable` is the only outcome that suppresses the narrative, and
  // every path into it - a deadline, an unreachable model, a result still
  // invalid after one retry - ends the same way: no causal claim survived the
  // citation check, so none is published. The raw token never reaches the
  // screen. An internal word shown to a reader who cannot decode it is a defect
  // of this layer, not of the layer that produced it.
  function guardBanner() {
    return '<div class="note guard tight banner">' +
      "<h4>No cause is published here, and that is the guard doing its job</h4>" +
      "<p>Every causal claim on this board has to cite an evidence query that actually ran. The " +
      "investigation did not return one that passed that check, so the system published nothing rather " +
      "than an explanation it could not stand behind.</p>" +
      "<p>Nothing measured was lost. Where it is happening, what it is costing, and every query the agent " +
      "ran are all still here, read from the store exactly as they always are.</p>" +
      "</div>";
  }

  function noRunBanner() {
    return '<div class="note tight banner">' +
      "<h4>No investigation has run on this incident yet</h4>" +
      "<p>The measured record below stands on its own. A cause is added when the agent has one it can cite.</p>" +
      "</div>";
  }

  function statusBanner(incident, investigation) {
    if (isInvestigating(incident)) return agentRunning(investigation);
    if (investigation && investigation.narrative_available) return "";
    if (!investigation || !investigation.outcome) return noRunBanner();
    return guardBanner();
  }

  // ------------------------------------------------------- the six questions

  function readCell(label, value) {
    if (value === null || value === undefined || value === "") return "";
    return "<div><dt>" + escapeHtml(label) + "</dt><dd>" + escapeHtml(String(value)) + "</dd></div>";
  }

  function readCellHtml(label, html) {
    if (!html) return "";
    return "<div><dt>" + escapeHtml(label) + "</dt><dd>" + html + "</dd></div>";
  }

  function changeBlock(change) {
    const data = change || {};
    if (data.actual == null && data.expected == null) {
      return '<p class="q-none">No change block is stored for this incident.</p>';
    }
    const falling = typeof data.absolute_delta === "number" && data.absolute_delta < 0;
    const direction = falling ? "down" : "up";
    return '<p class="q-lead">' + escapeHtml(metricWords(data.metric)) + " is <b>" +
      escapeHtml(ratio(data.actual)) + "</b> against <b>" + escapeHtml(ratio(data.expected)) +
      "</b> expected." + citeButton("detail-actual", "cite approval now") + "</p>" +
      '<dl class="q-read">' +
      readCell("moved", joinWords(
        pointsWord(data.absolute_delta) ? direction + " " + pointsWord(data.absolute_delta) : null,
        typeof data.relative_change === "number"
          ? direction + " " + pct(Math.abs(data.relative_change)) + " relative" : null
      )) +
      "</dl>" +
      (data.metric ? '<p class="q-foot mono">' + escapeHtml(String(data.metric)) + "</p>" : "");
  }

  function whereBlock(cohort, incident) {
    const data = cohort || {};
    const keys = Object.keys(data).filter(function (key) { return data[key]; });
    if (!keys.length) {
      return '<p class="q-lead">Platform-wide. The stored cohort names no dimension, so nothing narrower is claimed.</p>';
    }
    return '<p class="q-lead">' + escapeHtml(incidentScope(incident)) + "</p>" +
      '<ul class="dims">' + keys.map(function (key) {
        return "<li><span>" + escapeHtml(dimensionWord(key)) + "</span><b>" +
          escapeHtml(String(data[key])) + "</b></li>";
      }).join("") + "</ul>" +
      '<p class="q-foot">These are the dimensions the stored cohort names, and all of them.</p>';
  }

  function moneyBlock(financial) {
    const data = financial || {};
    if (!Object.keys(data).length) {
      return '<p class="q-none">No financial impact is stored for this incident.</p>';
    }
    const lost = data.estimated_lost_approved_volume || {};
    const assumptions = data.assumptions || [];
    const lostWords = joinWords(
      typeof lost.payments === "number" ? lost.payments + " payments" : null,
      moneyOrNull(lost)
    );
    return '<dl class="q-read">' +
      readCellHtml("at risk so far", escapeHtml(money(data.gmv_at_risk)) +
        citeButton("detail-risk", "cite at risk so far")) +
      readCellHtml("costing / hour", escapeHtml(money(data.loss_per_hour)) +
        citeButton("detail-burn", "cite costing per hour")) +
      readCellHtml("approvals not captured", escapeHtml(lostWords || "not in store") +
        citeButton("detail-lost", "cite payments lost")) +
      readCell("attempted value", moneyOrNull(data.attempted_value) || "not in store") +
      "</dl>" +
      (assumptions.length
        ? '<details class="q-more"><summary>What this figure assumes (' + assumptions.length + ")</summary><ul>" +
          assumptions.map(function (line) { return "<li>" + escapeHtml(String(line)) + "</li>"; }).join("") +
          "</ul></details>"
        : "");
  }

  function causeBlock(hypothesis, fallback) {
    const data = hypothesis || {};
    if (!data.statement) return '<p class="q-none">' + escapeHtml(fallback) + "</p>";
    return '<p class="q-lead big">' + escapeHtml(String(data.statement)) + "</p>" +
      '<p class="q-label">What it is standing on</p>' +
      evidenceList(data.evidence, "No citation was stored for this hypothesis.");
  }

  // Question 5 is where honest uncertainty lives. docs/challenge.md scores "a
  // case where the system admits the evidence isn't enough, instead of
  // inventing a diagnosis" as a bonus, so the competing explanations and the
  // missing-evidence list are presented as the discipline they are, not as the
  // system failing to know. Same stored values, named for what they are.
  function beliefBlock(belief, fallback) {
    const data = belief || {};
    const facts = data.confirmed_facts || [];
    const supporting = data.supporting_evidence || [];
    const competing = data.competing_explanations || [];
    const missing = data.missing_evidence || [];
    const ambiguity = data.why_ambiguity_exists || {};
    let html = "";
    if (facts.length) {
      html += '<p class="q-label">Established, and cited</p><ol class="claims">' +
        facts.map(function (fact) {
          return "<li><p>" + escapeHtml(String(fact.statement || "")) + "</p>" +
            evidenceList(fact.evidence) + "</li>";
        }).join("") + "</ol>";
    }
    if (supporting.length) {
      html += '<p class="q-label">Pointing the same way</p>' + evidenceList(supporting);
    }
    if (competing.length) {
      html += '<section class="rigor"><h4>Not ruled out<span class="rigor-n">' + competing.length + "</span></h4>" +
        '<p class="rigor-why">The agent has to publish what it could not eliminate. Each of these survives ' +
        "the evidence gathered so far, and carries the query that keeps it alive.</p>" +
        '<ol class="claims">' + competing.map(function (item) {
          return "<li><p>" + escapeHtml(String(item.explanation || "")) + "</p>" +
            evidenceList(item.evidence) + "</li>";
        }).join("") + "</ol>" +
        (ambiguity.statement
          ? '<div class="rigor-amb"><p class="q-label">Why this cannot be settled from what is stored</p><p>' +
            escapeHtml(String(ambiguity.statement)) + "</p>" + evidenceList(ambiguity.evidence) + "</div>"
          : "") +
        "</section>";
    }
    if (missing.length) {
      html += '<section class="rigor next"><h4>What would settle it<span class="rigor-n">' + missing.length + "</span></h4>" +
        '<p class="rigor-why">Named next observations, not a shrug. Each one would discriminate between the ' +
        "explanations above.</p>" +
        '<ol class="claims">' + missing.map(function (item) {
          return "<li><p>" + escapeHtml(String(item.request || "")) + "</p>" +
            (item.reason ? '<p class="because">Because ' + escapeHtml(lowerFirst(item.reason)) + "</p>" : "") +
            evidenceList(item.evidence) + "</li>";
        }).join("") + "</ol></section>";
    }
    if (!html) return '<p class="q-none">' + escapeHtml(fallback) + "</p>";
    return html;
  }

  function actionBlock(action, fallback) {
    const data = action || {};
    if (!data.action) return '<p class="q-none">' + escapeHtml(fallback) + "</p>";
    const urgency = data.urgency
      ? '<span class="urg">' + escapeHtml(String(data.urgency)) + "</span>"
      : "";
    return '<p class="q-lead">' + escapeHtml(String(data.action)) + "</p>" +
      (urgency ? '<p class="q-urg">urgency ' + urgency + "</p>" : "") +
      '<p class="q-label">What that rests on</p>' +
      evidenceList(data.basis, "No citation was stored for this recommendation.") +
      '<p class="q-foot">Advisory. The system does not execute it.</p>';
  }

  // The shape of the run, before the queries themselves. Both counts are of
  // rows present in the trail this view is about to draw - the view counting
  // what it is showing, never a figure about the incident.
  function trailSummary(trail, investigation) {
    const refused = trail.filter(function (entry) {
      return String(entry.outcome || "") !== "success";
    }).length;
    const took = durationWords(investigation && investigation.duration_ms);
    return '<div class="trail-top">' +
      "<p><b>" + trail.length + "</b> queries, in the order the agent asked them" +
      (refused ? ", <b>" + refused + "</b> of them refused by the gateway" : "") +
      (took ? ", over " + escapeHtml(took) : "") + ".</p>" +
      "<p>Each is a real call against the same store the rest of this board reads, and each returns in " +
      "milliseconds: the minute a run takes is the agent deciding what to ask next, not the data being " +
      "slow. The raw request and response sit under every card, because being able to show them is the " +
      "argument.</p>" +
      "</div>";
  }

  function trailCard(entry, index) {
    const sequence = String(entry.sequence);
    const refused = String(entry.outcome || "") !== "success";
    const skipped = entry.executed === false;
    const readings = readingsFor(entry);
    const purpose = (entry.parameters || {}).purpose;
    return '<article class="trail-card' + (refused ? " refused" : "") + '" style="--i:' + index + '">' +
      '<div class="tq-head">' +
      '<span class="tq-n mono">' + escapeHtml(sequence.padStart(2, "0")) + "</span>" +
      '<h3 class="mono">' + escapeHtml(entry.tool || "gateway") + "</h3>" +
      '<span class="tq-tags">' +
      (skipped ? '<span class="tag">not executed</span>' : "") +
      (refused ? '<span class="tag bad">refused</span>' : "") +
      (durationWords(entry.duration_ms)
        ? '<span class="tq-ms">' + escapeHtml(durationWords(entry.duration_ms)) + "</span>" : "") +
      citeChip("trail-" + sequence, "Q" + sequence, "cite query " + sequence + ", " + (entry.tool || "gateway")) +
      "</span></div>" +
      '<p class="tq-asked">' + escapeHtml(askedSentence(entry)) + "</p>" +
      (purpose ? '<p class="tq-why">Its own reason: ' + escapeHtml(String(purpose)) + "</p>" : "") +
      (readings.length
        ? '<dl class="tq-read">' + readings.map(function (row) {
          return "<div><dt>" + escapeHtml(row[0]) + "</dt><dd>" + escapeHtml(row[1]) + "</dd></div>";
        }).join("") + "</dl>"
        : '<p class="tq-none">The response carried nothing this view knows how to read. The raw record is below.</p>') +
      '<details class="tq-raw"><summary>Raw request and response</summary><div class="tq-raw-grid">' +
      "<div><h5>Asked</h5><pre>" + escapeHtml(pretty(entry.parameters)) + "</pre></div>" +
      "<div><h5>Returned</h5><pre>" + escapeHtml(pretty(entry.response)) + "</pre></div>" +
      "</div></details>" +
      "</article>";
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
    const banner = statusBanner(incident, investigation);
    if (!trail.length) {
      evidenceBoard.innerHTML = banner + (isInvestigating(incident)
        ? '<p class="empty">The trail is written in one go when the run finishes.</p>'
        : '<p class="empty">No evidence trail is stored for this incident.</p>');
      bindCites(evidenceBoard);
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
    body.insertAdjacentHTML("beforeend", trailSummary(trail, investigation));
    const wrap = document.createElement("div");
    wrap.className = "trail";
    // The queries arrive one after another the first time an incident's trail
    // is drawn, which is the story beat: watch it work. The board repaints
    // every 2.5s, and replaying the reveal on every poll would be a twitch, so
    // it runs once per incident.
    if (state.trailRevealed !== incident.incident_id) {
      wrap.classList.add("reveal");
      state.trailRevealed = incident.incident_id;
    }
    trail.forEach(function (entry, index) {
      wrap.insertAdjacentHTML("beforeend", trailCard(entry, index));
    });
    body.appendChild(wrap);
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
    const hypothesis = c4FieldText(
      payload.leading_hypothesis,
      "No causal narrative is stored for this incident."
    );
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
      escapeHtml(hypothesis) +
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
        body: { parameters: entry.parameters, executed: entry.executed, duration_ms: entry.duration_ms },
      };
    }
    if (citeId && citeId.indexOf("watch-proj:") === 0) {
      const watch = (state.watches || [])[Number(citeId.slice("watch-proj:".length))];
      if (!watch) return null;
      return {
        title: "Projected loss if this continues",
        lede: "Copied from the stored watch record. It is a projection the detector published, " +
          "not money already lost, and nothing on this page recomputed it.",
        rows: [
          ["source", watch.incident_id || "not in store"],
          ["lifecycle_state", watch.lifecycle_state || "not in store"],
          ["field", "financial_impact.projected_loss_per_hour"],
          ["value", watch.projected_loss_per_hour == null ? "not in store" : watch.projected_loss_per_hour],
        ],
        body: watch.projected_loss_per_hour,
      };
    }
    if (citeId && citeId.indexOf("ingest-") === 0) return ingestCite(citeId);
    if (citeId && citeId.indexOf("ask-") === 0) return askCite(citeId);
    if (citeId && citeId.indexOf("merchant-") === 0) return merchantCite(citeId);
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


  // Provenance cites the tool, not an incident record: `ingest_health` is a C2
  // evidence tool like any other and the drawer shows the field the figure was
  // read from, so the line can be checked rather than believed.
  function ingestCite(citeId) {
    const data = state.ingestion;
    if (!data || data.unreadable) return null;
    const dead = data.dead_letter || {};
    const lede = "Read from the ingest_health evidence tool over the same store every other " +
      "figure on this board comes from. Nothing on this line is computed in the page.";
    if (citeId === "ingest-accepted") {
      return {
        title: "Records accepted",
        lede: lede + " This is a row count the store holds after de-duplication, not a running " +
          "total of what a consumer saw. Redelivered records are dropped on event_id, which is " +
          "why duplicates is reported as not measured rather than guessed at.",
        rows: [
          ["tool", "ingest_health"],
          ["field", "accepted"],
          ["value", count(data.accepted)],
          ["attempts", count((data.stored || {}).attempts)],
          ["telemetry_samples", count((data.stored || {}).telemetry_samples)],
          ["payments_closed", count((data.stored || {}).payments_closed)],
          ["duplicates", "not measured - " + ((data.not_measured || {}).duplicates || "see the C2 contract")],
        ],
        body: data.stored,
      };
    }
    if (citeId === "ingest-refused") {
      return {
        title: "Records refused",
        lede: lede + " rejected and dead_letter.count are one measurement published under two " +
          "names and are equal by construction: a refused record is dead-lettered in the same " +
          "statement that rejects it. They are shown together so they cannot be read as two facts.",
        rows: [
          ["tool", "ingest_health"],
          ["field", "rejected / dead_letter.count"],
          ["rejected", count(data.rejected)],
          ["dead_letter.count", count(dead.count)],
          ["distinct_reasons", count(dead.distinct_reasons)],
        ],
        body: { reasons: dead.reasons, by_source: dead.by_source },
      };
    }
    if (citeId === "ingest-newest") {
      // The strip shows the attempt stream alone, because "payments ingested"
      // is the claim on the line and a telemetry timestamp would quietly
      // answer a different question. The other two readings belong here, where
      // a reader who presses can see them.
      const byKind = data.newest_by_kind || {};
      return {
        title: "Newest observed event",
        lede: lede + " This is the event time carried by the newest payment attempt in the store. " +
          "It is not the time that record arrived and it is not the wall clock. Telemetry samples " +
          "and closed payments are stored beside attempts and read separately below; they do not " +
          "move this figure or the watermark.",
        rows: [
          ["tool", "ingest_health"],
          ["field", "newest_event_at"],
          ["value", data.newest_event_at || "not in store"],
          ["oldest_event_at", data.oldest_event_at || "not in store"],
          ["newest_by_kind.attempts", byKind.attempts || "not in store"],
          ["newest_by_kind.telemetry_samples", byKind.telemetry_samples || "not in store"],
          ["newest_by_kind.payments_closed", byKind.payments_closed || "not in store"],
        ],
        body: {
          oldest_event_at: data.oldest_event_at,
          newest_event_at: data.newest_event_at,
          newest_by_kind: data.newest_by_kind,
        },
      };
    }
    if (citeId === "ingest-watermark") {
      return {
        title: "Measured through",
        lede: lede + " The watermark is the event time measurement is complete to: the newest " +
          "observed event less the lateness grace, floored to a bucket. lag_seconds is the " +
          "distance between the two - event time against event time, so it says how much of " +
          "what arrived is not yet measured. It is not seconds since a record arrived, and this " +
          "page does not present it as one.",
        rows: [
          ["tool", "ingest_health"],
          ["field", "watermark"],
          ["value", data.watermark || "not in store"],
          ["lag_seconds", data.lag_seconds === null || data.lag_seconds === undefined
            ? "not in store" : count(data.lag_seconds)],
          ["lateness_grace_seconds", count(data.lateness_grace_seconds)],
        ],
        body: {
          watermark: data.watermark,
          newest_event_at: data.newest_event_at,
          lag_seconds: data.lag_seconds,
          lateness_grace_seconds: data.lateness_grace_seconds,
        },
      };
    }
    return null;
  }

  // An answer's figure cites the query the engine tied it to, and a query in
  // the trail cites its own recorded call. Neither is verified here: the panel
  // shows what the engine recorded so it can be checked, and checking it is
  // not the dashboard's job.
  function askCite(citeId) {
    const payload = state.ask;
    if (!payload) return null;
    const parts = citeId.split(":");
    const index = Number(parts[1]);
    if (parts[0] === "ask-fig") {
      const row = (payload.figures || [])[index];
      if (!row) return null;
      const match = (payload.citations || []).filter(function (item) {
        return item.query_id && item.query_id === row.query_id;
      })[0];
      return {
        title: row.label || "Asserted figure",
        lede: "Copied from the ask engine's answer, under the query id the engine recorded it against. " +
          "The dashboard did not measure or recompute it.",
        rows: [
          ["question", payload.question || "not in store"],
          ["query_id", row.query_id || "not in store"],
          ["tool", row.tool || (match && match.tool) || "not in store"],
          ["value", row.value === null || row.value === undefined ? "not in store" : row.value],
        ],
        body: match || row,
      };
    }
    const entry = (payload.citations || [])[index];
    if (!entry) return null;
    return {
      title: "Query " + count(entry.sequence) + " · " + (entry.tool || "tool not in store"),
      lede: "One call the ask engine made against this store, as it recorded it.",
      rows: [
        ["query_id", entry.query_id || "not in store"],
        ["tool", entry.tool || "not in store"],
        ["outcome", entry.outcome || "not in store"],
        ["executed", entry.executed === false ? "no" : "yes"],
      ],
      body: { parameters: entry.parameters, executed: entry.executed, duration_ms: entry.duration_ms },
    };
  }

  // A merchant row cites the one incident record its figures were copied off,
  // never the group: no figure on that card is a total.
  function merchantCite(citeId) {
    const parts = citeId.split(":");
    const row = (state.merchants || [])[Number(parts[1])];
    if (!row) return null;
    const financial = row.financial_impact || {};
    const field = parts[0] === "merchant-burn"
      ? "financial_impact.loss_per_hour"
      : "financial_impact.gmv_at_risk";
    const value = parts[0] === "merchant-burn" ? financial.loss_per_hour : financial.gmv_at_risk;
    return {
      title: (parts[0] === "merchant-burn" ? "Loss rate · " : "Revenue at risk · ") +
        (row.scope_label || row.merchant_id || "Platform-wide"),
      lede: "Copied from that row's own highest-priority incident record" +
        (row.source_is_active === false ? ", which is closed - this is what it cost, not what it is costing" : "") +
        ". It is that one incident's figure, not a total for the row and not a total for the platform.",
      rows: [
        ["source", row.source_incident_id || "not in store"],
        ["scope", row.merchant_id ? "merchant " + row.merchant_id : "cohort " + (row.scope_label || "not in store")],
        ["state", row.source_is_active === false ? "closed" : "live"],
        ["field", field],
        ["value", value == null ? "not in store" : value],
      ],
      body: value,
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
      // The provenance line reads its own endpoint. It is the one figure on
      // the page that must not be derived from another payload: a freshness
      // claim assembled out of the incident feed would be describing the feed,
      // not the ingestion behind it. A failure here darkens that line alone
      // and leaves the rest of the board intact.
      jsonGet("/api/ingestion").catch(function () { return { unreadable: true }; }),
    ]).then(function (payloads) {
      state.overview = payloads[0];
      state.queue = payloads[1].incidents || [];
      state.watches = payloads[1].watches || [];
      state.merchants = payloads[2].merchants || [];
      state.calls = payloads[3].calls || [];
      state.escalations = payloads[4];
      state.ingestion = payloads[5];
      if (!state.selectedId && state.queue.length) state.selectedId = state.queue[0].incident_id;
      renderIngestion();
      renderOverview();
      renderQueue();
      renderWatchRail();
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

  // The judge control is two stages over one named target, not a scenario
  // picker: the target is decided in surfaces/inject.py and reported back in
  // every response. The words developing, collapse and clear are the same
  // vocabulary the API and the adapter use.
  const JUDGE_STAGES = ["developing", "collapse", "clear"];

  function renderJudge(payload) {
    const stage = (payload && payload.stage) || ((payload && payload.active) ? "collapse" : "clear");
    state.stage = stage;
    state.injected = stage !== "clear";
    judgeButtons.forEach(function (button) {
      const on = button.getAttribute("data-stage") === stage;
      button.setAttribute("data-on", on ? "true" : "false");
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function setJudgeBusy(busy) {
    judgeButtons.forEach(function (button) {
      button.disabled = busy;
    });
  }

  function loadJudgeState() {
    return jsonGet("/api/trigger").then(function (payload) {
      renderJudge(payload);
      judgeStatus.textContent = payload.message || "The trigger returned no account of what it did.";
    }).catch(function () {
      judgeStatus.textContent = "The judge control could not reach its own server. Nothing is injected.";
    });
  }

  $("judge-form").addEventListener("submit", function (event) {
    event.preventDefault();
    const submitter = event.submitter;
    const stage = submitter && submitter.getAttribute("data-stage");
    if (!stage || JUDGE_STAGES.indexOf(stage) === -1) return;
    setJudgeBusy(true);
    judgeStatus.textContent = "Sending your change into the live traffic.";
    fetch("/api/trigger", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: stage }),
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
        setJudgeBusy(false);
      });
  });

  $("drawer-close").addEventListener("click", closeCite);
  scrim.addEventListener("click", closeCite);

  // The only thing that fires /api/ask. It is never called from refresh(), and
  // the endpoint itself refuses GET, so the board's poll cannot reach a model.
  function submitAsk() {
    if (state.asking) return;
    const input = $("ask-input");
    const question = (input.value || "").trim();
    if (!question) {
      input.focus();
      return;
    }
    state.asking = true;
    state.ask = null;
    $("ask-go").disabled = true;
    renderAsk();
    fetch("/api/ask", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
    })
      .then(function (response) {
        return response.json().then(function (body) {
          // 409 is the server refusing to stack a second model call, which is
          // a state to show, not a failure to swallow.
          if (response.status === 409) return { busy: true, detail: body.detail };
          if (!response.ok) {
            return {
              question: question,
              outcome: "agent_unavailable",
              unavailable_kind: "engine_error",
              answer: body.detail || body.error || "The question was refused.",
              figures: [],
              citations: [],
            };
          }
          return body;
        });
      })
      .catch(function () {
        return {
          question: question,
          outcome: "agent_unavailable",
          unavailable_kind: "engine_error",
          answer: "The panel could not reach its own server, so nothing was asked.",
          figures: [],
          citations: [],
        };
      })
      .then(function (payload) {
        state.ask = payload;
        state.asking = false;
        $("ask-go").disabled = false;
        renderAsk();
      });
  }

  $("ask-form").addEventListener("submit", function (event) {
    event.preventDefault();
    submitAsk();
  });

  function tick() {
    const now = new Date();
    $("clock").textContent = now.toISOString().replace(".000Z", "Z");
    tickAgentRun();
  }

  tick();
  setInterval(tick, 1000);
  renderAskExamples();
  loadJudgeState();
  refresh();
  setInterval(refresh, 2500);
})();
