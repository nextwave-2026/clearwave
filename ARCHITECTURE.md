# Architecture

```mermaid
flowchart LR
    World[Simulated world] --> Stream[Observable event stream]
    World --> Truth[Quarantined hidden ground truth]
    Truth --> Evaluator[Evaluator]
    Stream --> Detection[Deterministic detection plane]
    Detection --> Incidents[Incident store]
    Incidents --> Investigator[Investigation agent]
    External[External corroboration] --> Investigator
    Investigator --> Diagnosis[Investigation result]
    Incidents --> Dashboard[Dashboard]
    Diagnosis --> Dashboard
    Diagnosis --> Escalation[Escalation binding]
    Escalation --> Slack[Slack notification]
    Escalation --> Phone[Phone escalation]
    Diagnosis --> Evaluator
```

This shows the settled product architecture. The technology choices behind each node are open.
Node boundaries correspond to the workstreams in [`docs/ownership.md`](docs/ownership.md).
