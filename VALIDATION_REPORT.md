# Package validation report

Date 05/11/2026.

- templates/new-agent/agent.json passed
- templates/existing-agent/agent.json passed
- templates/admin/deployment-binding.example.json passed
- templates/admin/existing-observe-binding.example.json passed
- templates/actions/action-proposal.example.json passed
- templates/phase2/experiment.example.json passed
- examples/task-request.json passed
- examples/task-envelope.json passed
- examples/result.json passed
- examples/ppm-input.json passed
- examples/ppm-output.json passed
- typed event fixtures passed
- untrusted tenant claim correctly rejected
- progress overflow correctly rejected
- zero denominator correctly rejected
- timestamp without timezone correctly rejected
- unknown cost rendered as zero correctly rejected
- observe-only control escalation correctly rejected
- external boundary claims governed mode correctly rejected
- invalid action digest correctly rejected
- identical optimization candidate correctly rejected

Local schema references, Python syntax and Markdown fences passed.

Runtime conformance, security, recovery, vendor compatibility, model quality, HUD load and live integration are pending implementation. The negative cases above validate package-level structural/semantic rules only.

Validator dependency jsonschema 4.26.0.
