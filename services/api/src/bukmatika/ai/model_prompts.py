from bukmatika.ai.gateway import ModelTask


def system_prompt_for_task(task: ModelTask) -> str:
    if task is ModelTask.RESEARCH_ANSWER:
        return (
            "Return only JSON matching the supplied schema. Use only the evidence included in the "
            "request. Every claim must cite one or more supplied evidence_id values. Do not invent "
            "evidence IDs, source coordinates, facts, or citations. If the evidence is "
            "insufficient, return a claim that states the limitation and cite the evidence that "
            "establishes the available context."
        )
    if task is ModelTask.RUNTIME_SMOKE:
        return (
            "This is a runtime verification request. Return only JSON matching the supplied schema "
            "and copy the requested verification values exactly. Do not add commentary."
        )
    return (
        "Return only JSON matching the supplied schema. Follow the request exactly and do not "
        "invent product state, capabilities, identifiers, or policy decisions."
    )
