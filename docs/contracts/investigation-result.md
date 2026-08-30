# C4 - Investigation result

C4 is produced by the investigation agent from one C3 record and C2 evidence responses. It
explains why a cause is plausible, what remains uncertain, and what to investigate next. It never
changes the record's business priority.

The same shape is used for a watch and for a confirmed incident. A watch investigation must not
assert that something has failed and must not assert a root cause the evidence does not support.
Weak evidence is stated immediately through `diagnostic_confidence`, `competing_explanations`,
`why_ambiguity_exists` and `missing_evidence`. There is no parallel watch schema.

## Shape

```json
{
  "incident_id": "inc-2026-08-29-001",
  "confirmed_facts": [
    {
      "statement": "Payment-level approval conversion is 64%, down from the 92% baseline.",
      "evidence": [
        {
          "claim": "The affected cohort has 640 approved payments out of 1000.",
          "query_id": "q_cohort_metrics_affected1",
          "tool": "cohort_metrics"
        }
      ]
    }
  ],
  "leading_hypothesis": {
    "statement": "Provider P2 degradation is the leading explanation for the affected slice.",
    "evidence": [
      {
        "claim": "P2 has a 35% timeout rate and elevated latency while its P3 sibling is healthy.",
        "query_id": "q_operational_metrics_affected1",
        "tool": "operational_metrics"
      }
    ]
  },
  "supporting_evidence": [
    {
      "claim": "Timeouts are 71.13% of failed attempts versus 10% in the baseline.",
      "query_id": "q_decline_breakdown_affected1",
      "tool": "decline_breakdown"
    }
  ],
  "competing_explanations": [
    {
      "explanation": "Bank X over-decline cannot be ruled out.",
      "evidence": [
        {
          "claim": "Provider P2 and Bank X are structurally inseparable in the observed window.",
          "query_id": "q_confounding_check_affected1",
          "tool": "confounding_check"
        }
      ]
    }
  ],
  "why_ambiguity_exists": {
    "statement": "There is no P2 traffic from another issuer and no Bank X traffic through another provider.",
    "evidence": [
      {
        "claim": "The provider/issuer cross-tab has only one observed mapping per value.",
        "query_id": "q_confounding_check_affected1",
        "tool": "confounding_check"
      }
    ]
  },
  "missing_evidence": [
    {
      "request": "Compare P2 traffic from another issuer or Bank X traffic through another provider.",
      "reason": "Either comparison would discriminate the two leading explanations.",
      "evidence": [
        {
          "claim": "The current cross-tab is structurally inseparable.",
          "query_id": "q_confounding_check_affected1",
          "tool": "confounding_check"
        }
      ]
    }
  ],
  "diagnostic_confidence": "medium",
  "recommended_next_action": {
    "action": "Investigate Provider P2 and collect a discriminatory provider/issuer comparison before broad rerouting.",
    "urgency": "now",
    "basis": [
      {
        "claim": "The affected cohort has an estimated $112000 per hour in GMV at risk.",
        "query_id": "q_financial_impact_affected1",
        "tool": "financial_impact"
      }
    ]
  }
}
```

### Field definitions

- `incident_id` (string) references the C3 incident being investigated.
- `confirmed_facts` (array) states observations accepted as facts. Each item has a `statement` and
  an `evidence` array.
- `leading_hypothesis` (object) has a bounded causal `statement` and supporting `evidence`.
- `supporting_evidence` (array) lists evidence that strengthens the leading hypothesis.
- `competing_explanations` (array) lists plausible alternatives. Each item has an `explanation`
  and its own evidence.
- `why_ambiguity_exists` (object) explains unresolved attribution and cites the evidence for that
  explanation.
- `missing_evidence` (array) contains concrete requests for the next discriminatory observation;
  each request also cites the evidence that makes it necessary.
- `diagnostic_confidence` is qualitative (`low`, `medium`, or `high`). It describes causal evidence,
  not business urgency, and must not be fabricated as a precise probability.
- `recommended_next_action` contains an advisory `action`, `urgency`, and evidence-backed `basis`.
  The system does not execute the action.
- Every object in an evidence or basis array is an evidence item and must contain the exact
  `query_id` returned by the C2 call that supports its claim. A narrative claim without a cited
  query is not an evidence-backed claim.

C4 deliberately has no `severity` field. Business priority remains on C3, while diagnostic
confidence remains here; one must not be used as a substitute for the other.
