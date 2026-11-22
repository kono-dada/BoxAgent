"""Jev-Mem's Jev questions, using the official SDK primitives.

All instructions identify their state fields: question IDs are NOT model input.
Noul outputs are probabilities of defined propositions, not graded importance.
"""

from typesafe_sdk import Choice, Noul


def noul(instructions, yes, no):
    return Noul(instructions=instructions, criteria={"true": yes, "false": no})


def observation_questions():
    """Admission and multi-label typing share state and can be evaluated together."""
    return {**admission_questions(), **memory_type_questions()}


def admission_questions():
    return {
        "should_store": noul(
            "Does `observation` contain a specific detail worth retaining for future conversation recall?",
            "A fact, experience, plan, preference, procedure or unresolved need attributable to a participant.",
            "Only a greeting, acknowledgement, generic pleasantry, or content with no recallable detail."),
        "future_utility": noul(
            "Could a future question about a participant or their activities be answered using a concrete detail in `observation`?",
            "Identifies at least one concrete detail useful to a plausible future recall question.",
            "No specific detail would help answer a future recall question."),
        "importance": noul(
            "Does `observation` describe a participant's meaningful life event, commitment, goal, relationship, constraint, or stable preference?",
            "At least one of these personally meaningful details is stated; ordinary personal facts can qualify.",
            "Only conversational filler or incidental wording, without any of these details."),
        "novelty": noul(
            "Does `observation` add a fact absent from `recent_memories`, given the `exact_duplicate` flag?",
            "At least one new detail, correction or time-specific update; no exact duplicate. Novelty is relative to the supplied memories only.",
            "An exact duplicate, or all recallable details are already in the supplied recent memories."),
        "redundancy": noul(
            "Is all recallable information in `observation` already present in `recent_memories`, or is `exact_duplicate` true?",
            "An exact duplicate or a paraphrase with no new detail, correction or temporal update.",
            "Adds any new detail or meaningful update; shared topic alone does not imply redundancy."),
    }


def memory_type_questions():
    """Independent types; these scores never determine whether to store a turn."""
    return {
        "episodic": noul(
            "Does `observation` describe a particular experience or event involving a participant?",
            "A specific past, current or planned event, even if its exact time is unstated.",
            "Only a general fact, procedure or preference with no particular event."),
        "semantic": noul(
            "Does `observation` state a fact about a person, entity or the world that remains useful beyond this conversational turn?",
            "An attributable fact or relationship, even when it also appears in an episodic account.",
            "Only a transient conversational acknowledgement or a question with no asserted fact."),
        "procedural": noul(
            "Does `observation` describe how to carry out a task?",
            "An instruction, ordered step, method or actionable rule for performing a task.",
            "Merely mentions doing a task without describing how."),
        "preference": noul(
            "Does `observation` express a participant's preference, aversion or habitual choice?",
            "An attributable like, dislike, preferred option or habitual choice.",
            "An isolated action alone, another person's unattributed preference, or no preference evidence."),
    }


def relation_questions(index, infer_identity=True):
    pair = f"Compare `new_memory.content` with `candidates[{index}].content`. "
    questions = {
        "semantic": noul(pair + "Would a semantic link between these observations help retrieve a shared specific topic or fact?",
            "A specific shared topic, fact or event makes the connection useful.",
            "Only generic conversational vocabulary or no meaningful semantic connection."),
        "causes": noul(pair + "Does the event in `new_memory.content` cause, enable or explain the candidate event?",
            "The supplied accounts support this direction of causal influence.",
            "Only similarity, chronology, a shared entity, or insufficient causal evidence."),
        "caused_by": noul(pair + "Does the candidate event cause, enable or explain the event in `new_memory.content`?",
            "The supplied accounts support this direction of causal influence.",
            "Only similarity, chronology, a shared entity, or insufficient causal evidence."),
    }
    if infer_identity:
        questions["entity"] = noul(pair + f"Using `new_memory.entities` and `candidates[{index}].entities`, do any names or aliases refer to the same real-world entity?",
            "Context supports a shared identity despite differing names or aliases.",
            "Distinct entities or insufficient evidence to resolve the alias; similar names alone are insufficient.")
    return questions


def consolidation_questions(index):
    pair = f"Compare `new_memory.content` with `candidates[{index}].content`. "
    return {
        "redundant": noul(pair + "Do these observations repeat the same fact with no additional recallable detail?",
            "One is a duplicate or paraphrase without a new detail or time-specific update.",
            "They provide different details or describe distinct occurrences."),
        "contradiction": noul(pair + "Do these accounts assert incompatible facts about the same subject at the same time?",
            "Claims cannot both hold at the stated time and context.",
            "Compatible claims, uncertainty, or a change over time that explains the difference."),
        "obsolete": noul(pair + "Does the new memory explicitly replace the candidate's previously valid fact with an updated fact?",
            "An explicit update supersedes the earlier fact for current-state questions.",
            "No explicit replacement; mere recency or a separate event is insufficient."),
        "link": noul(pair + "Would following a link between these observations help answer a future recall question?",
            "The connection supplies related, corroborating, correcting or contrasting evidence.",
            "There is no specific connection useful for recall."),
        "representation": Choice(
            instructions=pair + "Which representation best fits the relationship between these two observations? Judge from the supplied accounts; do not assume answers to other questions.",
            criteria={
                "keep_separate": "Contradictory accounts, unique details that a combined representation would lose, or distinct facts/events without a supported general pattern.",
                "merge": "Compatible accounts of the same fact or event can be combined without losing unique details.",
                "promote": "Distinct repeated episodes explicitly support a stable general pattern suitable for semantic abstraction; prefer this over merge for repeated events.",
                "uncertain": "Insufficient evidence to choose a safe combined or separate representation.",
            }),
    }


def routing_questions():
    return {
        "semantic": noul("Would finding topically or semantically related memories help answer `query`?",
                         "Recall of related facts is useful.", "No related-memory lookup is needed."),
        "temporal": noul("Does answering `query` require event dates, durations, ordering or changes over time?",
                         "A time relation is needed to answer correctly.", "Dates or ordering are incidental to the answer."),
        "causal": noul("Does answering `query` require explaining a cause, motivation, enabling condition or effect?",
                       "Causal or explanatory evidence is needed.", "Only factual association or chronology is requested."),
        "entity": noul("Would connecting mentions of the same person, place, object or organization help answer `query`?",
                       "Combining entity-specific facts or aliases is useful.", "Entity identity is irrelevant to the answer."),
        "multi_hop_need": noul("Does `query` require combining at least two distinct pieces of remembered evidence?",
                               "The question asks for a comparison, aggregation or chained inference.", "One direct remembered fact suffices."),
        "recency_importance": noul("Does `query` require the latest applicable fact rather than a historical fact?",
                                   "Current state, latest update or recent status is requested.", "Historical or timeless facts answer the question."),
    }


def stopping_questions():
    return {
        "evidence_sufficient": noul("Does `evidence` contain support for every factual part of an answer to `query`?",
            "A grounded answer can be given from these memories without inventing missing facts.",
            "Any required fact or reasoning link is unsupported; related topics alone are insufficient."),
        "continue_useful": noul("Given `query` and `evidence`, is another retrieval round likely to fill a specific gap or resolve a conflict?",
            "An identifiable missing fact or conflict could benefit from more memory retrieval.",
            "No identifiable retrieval need remains or more memories are unlikely to help."),
        "missing_evidence": noul("Is at least one fact required by `query` absent from `evidence`?",
            "A required detail, date, identity, count or linking fact is not supported.",
            "All required facts have explicit support in the supplied evidence."),
        "contradiction": noul("Does `evidence` contain conflicting claims relevant to `query` that the supplied time/context cannot reconcile?",
            "A conflict still affects which answer is correct.",
            "Claims agree, differ only by explained temporal updates, or do not affect the answer."),
    }


def traversal_questions(index):
    candidate = f"candidates[{index}]"
    return {
        "relevance": noul(f"Does `{candidate}.content` contain a fact needed to answer `query`?",
            "Direct answer evidence or a necessary intermediate fact.", "Only topic overlap or unrelated content."),
        "relation_usefulness": noul(f"Does the stated graph relation of `{candidate}` connect `evidence` to information useful for `query`?",
            "The relation and its direction support an answer-relevant connection.", "A graph edge exists but has no demonstrated usefulness for this question."),
        "new_information": noul(f"Does `{candidate}.content` add an answer-relevant detail absent from `evidence`?",
            "A distinct relevant detail or missing reasoning link.", "Only duplicated evidence or irrelevant new details."),
        "supports_current_evidence": noul(f"Does `{candidate}.content` independently corroborate a claim in `evidence` relevant to `query`?",
            "Provides compatible corroborating evidence for a specific claim.", "No specific corroboration, or contradicts that claim."),
    }


def choice_fixture(question, option):
    """Explicit one-hot mock answer, never used as a live fallback."""
    return {"type": "choice", "choice": option,
            "probabilities": {key: float(key == option) for key in question.criteria}, "confidence": 1.0}
