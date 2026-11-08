"""Owner-approved, metadata-only coordination of exactly two named Agents.

This is intentionally a completion-evidence handoff, NOT cross-Agent retrieval,
shared memory, instruction forwarding or autonomous spawning. The existing
durable queue performs all actual owner-ticked read-only plan execution.
"""
from local_agent_profiles import AGENT_ID
from local_scoped_agent_memory import PROJECT_ID


class DelegationError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def _validate_fields(delegation):
    if (type(delegation) is not dict or set(delegation) != {
            "project_id", "source_agent_id", "target_agent_id",
            "handoff_mode"}):
        raise DelegationError("invalid_delegation_fields", 422)
    project = delegation["project_id"]
    source = delegation["source_agent_id"]
    target = delegation["target_agent_id"]
    if (type(project) is not str or PROJECT_ID.fullmatch(project) is None
            or type(source) is not str or AGENT_ID.fullmatch(source) is None
            or type(target) is not str or AGENT_ID.fullmatch(target) is None
            or source == target
            or delegation["handoff_mode"] != "COMPLETION_PROOF_ONLY"):
        raise DelegationError("invalid_delegation_scope", 422)
    return dict(delegation)


def validate_delegation(delegation, nodes, plans):
    """Validate same-project source/target plans and live least-privilege rules.

    nodes: exactly two {'plan_id', 'depends_on'} dicts ordered source -> target.
    Existing preapproved plans each have their OWN Agent identity and own notes.
    A new Agent cannot inherit a tool or an instruction from the source Agent.
    """
    meta = _validate_fields(delegation)
    if (type(nodes) is not list or len(nodes) != 2
            or type(nodes[0]) is not dict or type(nodes[1]) is not dict
            or nodes[0].get("depends_on") != []
            or nodes[1].get("depends_on") != [nodes[0].get("plan_id")]):
        raise DelegationError("delegation_requires_sequential_two_agent_plans", 422)
    if (plans.agent_profiles is None or plans.scoped_memory_store is None):
        raise DelegationError("delegation_dependencies_disabled", 403)
    for index, agent_id in enumerate((
            meta["source_agent_id"], meta["target_agent_id"])):
        try:
            plan = plans.get(nodes[index]["plan_id"])
            binding = plan["agent_binding"]
            refs = plan["scoped_memory_refs"]
            if (type(binding) is not dict
                    or binding.get("agent_id") != agent_id
                    or not refs
                    or any(x["project_id"] != meta["project_id"]
                           or x["agent_id"] != agent_id for x in refs)
                    or plan["memory_refs"]):
                raise DelegationError("delegation_plan_scope_mismatch", 403)
            # No shared or inherited tools, each profile's own policy applies.
            plans._verify_agent_binding(binding, plan["steps"])
            plans._verify_scoped_memory_refs(refs, binding)
        except DelegationError:
            raise
        except (KeyError, TypeError, ValueError):
            raise DelegationError("delegation_plan_scope_mismatch", 403) from None
        except Exception as exc:
            # Fail closed and expose no raw memory or profile content.
            raise DelegationError("delegation_authorization_changed", 409) from None
    return meta
