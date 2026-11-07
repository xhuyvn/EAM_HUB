"""Mock provider adapter for deterministic in-process testing (C05, C06, C21, C29, C29b, C38).

Features:
- Configurable failure switches: drop_ack, crash_after_send, slow_cancel, lookup_supported.
- Action execution (create_pr) accepting a client token (operation_key).
- Observable counters: submissions[idempotency_key], prs_created.
- Status lookups and cancellation.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class MockProviderError(Exception):
    """Base error for MockProvider."""



class MockProviderAckDroppedError(MockProviderError):
    """Simulated network or process failure dropping provider acknowledgement."""



class MockProviderCrashError(MockProviderError):
    """Simulated crash immediately after dispatch."""



class MockProviderLookupUnsupportedError(MockProviderError):
    """Lookup operation is not supported by this provider (DG1-C2, C38)."""



@dataclass
class MockProvider:
    """Deterministic in-process provider with fault injection switches."""

    drop_ack: bool = False
    crash_after_send: bool = False
    slow_cancel: bool = False
    lookup_supported: bool = True

    # Observable counters and ledgers
    submissions: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    prs_created: int = 0
    prs: dict[str, dict[str, Any]] = field(default_factory=dict)
    submitted_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    cancelled_executions: set[str] = field(default_factory=set)

    def submit(
        self,
        task_envelope: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str,
        client_token: str | None = None,
    ) -> dict[str, Any]:
        """Submit a task/run to the provider.

        Increments submissions counter by idempotency_key.
        Applies crash_after_send or drop_ack switches.
        """
        self.submissions[idempotency_key] = self.submissions.get(idempotency_key, 0) + 1

        exec_ref = f"mock-exec-{idempotency_key[:12]}"
        self.submitted_runs[idempotency_key] = {
            "execution_ref": exec_ref,
            "task_envelope": dict(task_envelope) if task_envelope else {},
            "client_token": client_token,
            "status": "running",
            "submitted_at": dt.datetime.now(dt.UTC).isoformat(),
        }

        if self.crash_after_send:
            raise MockProviderCrashError(
                f"Simulated worker/provider crash after sending submission for {idempotency_key}"
            )

        if self.drop_ack:
            raise MockProviderAckDroppedError(
                f"Simulated ACK dropped by provider for submission {idempotency_key}"
            )

        return {
            "state": "accepted",
            "execution_ref": exec_ref,
            "idempotency_key": idempotency_key,
        }

    def lookup(self, idempotency_key: str) -> dict[str, Any]:
        """Look up remote execution status by dispatch idempotency_key.

        Returns:
        - {"state": "unsupported"} if lookup_supported is False (C38, DG1-C2)
        - {"state": "found", "execution_ref": ...} if submitted
        - {"state": "not_found"} if absent
        """
        if not self.lookup_supported:
            return {
                "state": "unsupported",
                "supported": False,
                "message": "Provider does not support execution status lookup",
            }

        if idempotency_key in self.submitted_runs:
            rec = self.submitted_runs[idempotency_key]
            return {
                "state": "found",
                "execution_ref": rec["execution_ref"],
                "status": rec["status"],
                "client_token": rec.get("client_token"),
            }

        return {
            "state": "not_found",
            "supported": True,
            "message": f"No submission found for idempotency key {idempotency_key}",
        }

    def cancel(self, execution_ref: str, command_id: str = "") -> dict[str, Any]:
        """Cancel an in-flight execution at the provider.

        If slow_cancel is set, simulates delayed confirmation.
        """
        self.cancelled_executions.add(execution_ref)
        if self.slow_cancel:
            return {
                "state": "cancelling",
                "acknowledged": True,
                "completed": False,
                "execution_ref": execution_ref,
                "command_id": command_id,
            }

        return {
            "state": "cancelled",
            "acknowledged": True,
            "completed": True,
            "execution_ref": execution_ref,
            "command_id": command_id,
        }

    def create_pr(
        self,
        *,
        client_token: str,
        repo: str = "example-repo",
        title: str = "Automated PR",
        arguments_sha256: str = "",
    ) -> dict[str, Any]:
        """Create a pull request using a client token (operation_key).

        Increments prs_created. Applies drop_ack if enabled.
        """
        self.prs_created += 1
        pr_number = 100 + self.prs_created
        pr_id = f"pr-{pr_number}"
        pr_record = {
            "pr_id": pr_id,
            "pr_number": pr_number,
            "client_token": client_token,
            "repo": repo,
            "title": title,
            "arguments_sha256": arguments_sha256,
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        self.prs[client_token] = pr_record

        if self.drop_ack:
            raise MockProviderAckDroppedError(
                f"Simulated ACK dropped after PR #{pr_number} creation for client token {client_token}"
            )

        return pr_record

    def lookup_pr(self, client_token: str) -> dict[str, Any]:
        """Look up a PR by client token (operation_key)."""
        if not self.lookup_supported:
            return {"state": "unsupported"}

        if client_token in self.prs:
            return {
                "state": "found",
                "pr": self.prs[client_token],
            }

        return {"state": "not_found"}

    def reset(self) -> None:
        """Reset counters and records."""
        self.submissions.clear()
        self.prs_created = 0
        self.prs.clear()
        self.submitted_runs.clear()
        self.cancelled_executions.clear()
